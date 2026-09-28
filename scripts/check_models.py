"""Check which model Gemini Web actually serves for each proxy model and record it in models.json.

Usage:
    python scripts/check_models.py                      # anonymous
    python scripts/check_models.py --config config.json # signed in via its cookie_file
    python scripts/check_models.py --cookie-file gemini-auth.json
    python scripts/check_models.py --docs-only          # only compare with the API model list

Writes the served model for each entry into gemini_web2api/models.json. A signed-in run also
removes entries that Gemini reroutes to another model. Then run scripts/update_readme.py.
Exits with 1 when a model is rerouted or fails, or the docs list a model we lack.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gemini_web2api.config import CONFIG, load_config
from gemini_web2api.gemini import (_build_headers, _build_payload, _get_httpx_client, _get_url,
                                   extract_response_text, fetch_latest_bl, load_cookie)
from gemini_web2api.models import MODELS_FILE

DOCS_URL = "https://ai.google.dev/gemini-api/docs/models.md.txt"
# Plain chat models only: gemini-3.8-flash, gemini-3.5-flash-lite, gemini-3.1-pro (no -tts, -live, -preview).
DOC_MODEL_RE = re.compile(r"\bgemini-(\d+(?:\.\d+)?)-(flash|pro)(-lite)?\b(?![\w.-])")
NAME_RE = re.compile(r"^gemini-(\d+(?:\.\d+)?)-(flash|pro)(-lite)?")
# ponytail: regex over the raw stream, parse the candidate array if Gemini starts quoting other labels
SERVED_RE = re.compile(r'\\"(\d+(?:\.\d+)? (?:Flash|Pro)[\w -]*)\\",true,')


def _version(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def new_docs_models(models: dict) -> list:
    """Models in the docs newer than the newest entry of the same family (flash/pro)."""
    newest = {}
    for name in models:
        if m := NAME_RE.match(name):
            newest[m.group(2)] = max(newest.get(m.group(2), ()), _version(m.group(1)))
    text = _get_httpx_client().get(DOCS_URL, timeout=30).text
    return sorted({m.group(0) for m in DOC_MODEL_RE.finditer(text)
                   if _version(m.group(1)) > newest.get(m.group(2), ())})


def served_label(cfg: dict) -> str:
    body = _build_payload("hello", cfg["mode"], cfg["think"], extra_fields=cfg.get("extra"))
    resp = _get_httpx_client().post(_get_url(), content=body, headers=_build_headers(cfg["mode"]))
    resp.raise_for_status()
    labels = SERVED_RE.findall(resp.text)
    if not extract_response_text(resp.text):
        return "(empty reply)"
    return labels[0] if labels else "(no label)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="config.json whose cookie_file is used to sign in")
    parser.add_argument("--cookie-file", help="gemini-auth.json to sign in with")
    parser.add_argument("--docs-only", action="store_true", help="skip the Gemini Web requests")
    args = parser.parse_args()

    data = json.loads(MODELS_FILE.read_text())
    models = data["models"]
    failed = False
    missing = new_docs_models(models)
    print(f"Models in {DOCS_URL} newer than models.json:")
    print("\n".join(f"  {m}" for m in missing) or "  none")
    failed |= bool(missing)
    if args.docs_only:
        return int(failed)

    if args.config:
        load_config(args.config)
    if args.cookie_file:
        CONFIG["cookie_file"] = args.cookie_file
    CONFIG["temporary_chats"] = True  # keep check prompts out of the account history
    load_cookie()
    CONFIG["gemini_bl"] = fetch_latest_bl() or CONFIG["gemini_bl"]
    mode = "signed-in" if load_cookie()[0] else "anonymous"
    print(f"\nServed models ({mode}):")
    for name, cfg in list(models.items()):
        want = cfg.get("label")
        try:
            got = served_label(cfg)
        except Exception as e:
            got = f"(error: {e})"
        cfg.setdefault("served", {})[mode] = got
        ok = not got.startswith("(") and (want is None or got == want)
        failed |= not ok
        print(f"  {'OK ' if ok else 'BAD'} {name:24} served {got:20} expected {want or 'any'}")
        # Only a clear reroute removes a model; errors may be transient and keep it.
        if mode == "signed-in" and want and not got.startswith("(") and got != want:
            del models[name]
            print(f"      removed {name}: rerouted to {got}")
    data["checked"][mode] = str(date.today())
    MODELS_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print(f"\nUpdated {MODELS_FILE.name}; run scripts/update_readme.py to refresh the README")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
