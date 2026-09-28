"""Model definitions, loaded from models.json (kept current by scripts/check_models.py)."""
import json
from pathlib import Path

# "mode" is the MODE_CATEGORY enum from Gemini's frontend JS:
#   1=FAST, 2=THINKING, 3=PRO, 4=AUTO, 5=FAST_DYNAMIC_THINKING, 6=FLASH_LITE
# "id" is the model ID the signed-in web app sends in the x-goog-ext-525001261-jspb header, captured
# from the browser. Anonymous requests ignore it; a model without one gets the account default.
MODELS_FILE = Path(__file__).with_name("models.json")
MODELS = json.loads(MODELS_FILE.read_text())["models"]
WEB_MODEL_IDS = {m["mode"]: m["id"] for m in MODELS.values() if m.get("id")}


def available_models(signed_in: bool) -> dict:
    """Models that answered as themselves in the last check for this state (anonymous or signed in).
    Models never checked in that state are included."""
    mode = "signed-in" if signed_in else "anonymous"
    return {n: c for n, c in MODELS.items()
            if c.get("label") is None or c.get("served", {}).get(mode, c["label"]) == c["label"]}


def resolve_model(model_name: str, default: str = "gemini-3.6-flash"):
    """Resolve model name to (name, mode_id, think_mode, error, extra_fields).

    Unknown model names fall back to default rather than erroring,
    since upstream clients may request arbitrary model identifiers.
    """
    think_override = None
    if "@think=" in model_name:
        model_name, think_str = model_name.rsplit("@think=", 1)
        try:
            think_override = int(think_str)
        except ValueError:
            return None, None, None, f"Invalid think level: {think_str}", None
    cfg = MODELS.get(model_name)
    if not cfg:
        from .gemini import log
        fallback = default if default in MODELS else "gemini-auto"
        log(f"Unknown model '{model_name}', falling back to '{fallback}'")
        model_name = fallback
        cfg = MODELS[model_name]
    mode_id = cfg["mode"]
    think_mode = think_override if think_override is not None else cfg["think"]
    extra = cfg.get("extra")
    return model_name, mode_id, think_mode, None, extra
