"""Rewrite the README's Supported Models lists from gemini_web2api/models.json.

Usage:
    python scripts/update_readme.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
MODELS_FILE = ROOT / "gemini_web2api" / "models.json"


def render(data: dict, mode: str) -> str:
    """Markdown: models that work, then models grouped by what they are rerouted to."""
    works, rerouted, fails = [], {}, []
    for name, cfg in data["models"].items():
        got, want = cfg.get("served", {}).get(mode), cfg.get("label")
        if got is None:
            continue
        if got.startswith("("):
            fails.append(f"- `{name}` {got}")
        elif want is None or got == want:
            works.append(f"- `{name}` ({got})")
        else:
            rerouted.setdefault(got, []).append(f"- `{name}`")
    parts = [f"Checked {data['checked'].get(mode, 'never')} with `scripts/check_models.py`."]
    if works:
        parts.append("Works:\n\n" + "\n".join(works))
    for label, names in rerouted.items():
        parts.append(f"Rerouted to {label}:\n\n" + "\n".join(names))
    if fails:
        parts.append("Fails:\n\n" + "\n".join(fails))
    return "\n\n".join(parts)


def replace_block(text: str, mode: str, block: str) -> str:
    """Replace the text between <!-- models:MODE:start --> and <!-- models:MODE:end -->."""
    start, end = f"<!-- models:{mode}:start -->", f"<!-- models:{mode}:end -->"
    a, b = text.index(start) + len(start), text.index(end)
    return f"{text[:a]}\n{block}\n{text[b:]}"


def main() -> int:
    data = json.loads(MODELS_FILE.read_text())
    text = README.read_text()
    for mode in ("anonymous", "signed-in"):
        text = replace_block(text, mode, render(data, mode))
    README.write_text(text)
    print(f"Updated {README.name} from {MODELS_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
