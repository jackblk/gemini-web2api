"""Check which model Gemini Web actually serves for each proxy model, and list new models.

Usage:
    python scripts/check_models.py                      # anonymous
    python scripts/check_models.py --config config.json # signed in via its cookie_file
    python scripts/check_models.py --cookie-file gemini-auth.json
    python scripts/check_models.py --docs-only          # only compare with the API model list
    python scripts/check_models.py --update-readme      # also rewrite the matching README block

Exits with 1 when a model is served as something else or the docs list a model we lack.
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gemini_web2api.config import CONFIG, load_config
from gemini_web2api.gemini import (_build_headers, _build_payload, _get_httpx_client, _get_url,
                                   extract_response_text, fetch_latest_bl, load_cookie)
from gemini_web2api.models import MODELS

README = Path(__file__).resolve().parent.parent / "README.md"
DOCS_URL = "https://ai.google.dev/gemini-api/docs/models.md.txt"
# Plain chat models only: gemini-3.8-flash, gemini-3.5-flash-lite, gemini-3.1-pro (no -tts, -live, -preview).
DOC_MODEL_RE = re.compile(r"\bgemini-(\d+(?:\.\d+)?)-(flash|pro)(-lite)?\b(?![\w.-])")
NAME_RE = re.compile(r"^gemini-(\d+(?:\.\d+)?)-(flash|pro)(-lite)?")
# ponytail: regex over the raw stream, parse the candidate array if Gemini starts quoting other labels
SERVED_RE = re.compile(r'\\"(\d+(?:\.\d+)? (?:Flash|Pro)[\w -]*)\\",true,')


def _version(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def new_docs_models() -> list:
    """Models in the docs newer than the newest MODELS entry of the same family (flash/pro)."""
    newest = {}
    for name in MODELS:
        if m := NAME_RE.match(name):
            newest[m.group(2)] = max(newest.get(m.group(2), ()), _version(m.group(1)))
    text = _get_httpx_client().get(DOCS_URL, timeout=30).text
    return sorted({m.group(0) for m in DOC_MODEL_RE.finditer(text)
                   if _version(m.group(1)) > newest.get(m.group(2), ())})


def expected_label(name: str):
    """'gemini-3.7-flash' -> '3.7 Flash', 'gemini-3.5-flash-lite' -> '3.5 Flash-Lite', else None."""
    m = NAME_RE.match(name)
    if not m:
        return None
    return f"{m.group(1)} {m.group(2).title()}{'-Lite' if m.group(3) else ''}"


def served_label(name: str) -> str:
    cfg = MODELS[name]
    body = _build_payload("hello", cfg["mode"], cfg["think"], extra_fields=cfg.get("extra"))
    resp = _get_httpx_client().post(_get_url(), content=body, headers=_build_headers(cfg["mode"]))
    resp.raise_for_status()
    labels = SERVED_RE.findall(resp.text)
    if not extract_response_text(resp.text):
        return "(empty reply)"
    return labels[0] if labels else "(no label)"


def render(results: list) -> str:
    """Markdown: models that work, then models grouped by what they are rerouted to."""
    works, rerouted, fails = [], {}, []
    for name, want, got in results:
        if got.startswith("("):
            fails.append(f"- `{name}` {got}")
        elif want is None or got == want:
            works.append(f"- `{name}` ({got})")
        else:
            rerouted.setdefault(got, []).append(f"- `{name}`")
    parts = [f"Checked {date.today()} with `scripts/check_models.py`."]
    if works:
        parts.append("Works:\n\n" + "\n".join(works))
    for label, names in rerouted.items():
        parts.append(f"Rerouted to {label}:\n\n" + "\n".join(names))
    if fails:
        parts.append("Fails:\n\n" + "\n".join(fails))
    return "\n\n".join(parts)


def update_readme(mode: str, block: str) -> None:
    """Replace the text between <!-- models:MODE:start --> and <!-- models:MODE:end -->."""
    start, end = f"<!-- models:{mode}:start -->", f"<!-- models:{mode}:end -->"
    text = README.read_text()
    a, b = text.index(start) + len(start), text.index(end)
    README.write_text(f"{text[:a]}\n{block}\n{text[b:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="config.json whose cookie_file is used to sign in")
    parser.add_argument("--cookie-file", help="gemini-auth.json to sign in with")
    parser.add_argument("--docs-only", action="store_true", help="skip the Gemini Web requests")
    parser.add_argument("--update-readme", action="store_true", help="write the results into README.md")
    args = parser.parse_args()

    failed = False
    missing = new_docs_models()
    print(f"Models in {DOCS_URL} newer than MODELS:")
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
    results = []
    for name in MODELS:
        want = expected_label(name)
        try:
            got = served_label(name)
        except Exception as e:
            got = f"(error: {e})"
        ok = got == want if want else not got.startswith("(")
        results.append((name, want, got))
        failed |= not ok
        print(f"  {'OK ' if ok else 'BAD'} {name:32} served {got:20} expected {want or 'any'}")
    if args.update_readme:
        update_readme(mode, render(results))
        print(f"\nUpdated the {mode} block in {README.name}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
