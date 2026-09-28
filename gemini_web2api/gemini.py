"""Gemini StreamGenerate protocol implementation over a shared httpx client."""
import contextvars
import json
import time
import uuid
import re
import urllib.parse
import hashlib
import logging
from pathlib import Path

import httpx

from .config import CONFIG

logger = logging.getLogger("gemini_web2api")
# Set per request by the server (one thread per request); log() prefixes it.
request_id = contextvars.ContextVar("request_id", default=None)
_cookie_cache = {"str": "", "sapisid": None, "mtime": 0}
_httpx_client = None

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def log(msg: str):
    if CONFIG["log_requests"]:
        rid = request_id.get()
        logger.info(f"[{rid}] {msg}" if rid else msg)


def _get_httpx_client() -> httpx.Client:
    """Shared client for every upstream request. Without a configured proxy, httpx uses the
    HTTP(S)_PROXY environment variables."""
    global _httpx_client
    if _httpx_client is None:
        proxy = CONFIG.get("proxy")
        _httpx_client = httpx.Client(
            transport=httpx.HTTPTransport(proxy=proxy) if proxy else None,
            timeout=CONFIG["request_timeout_sec"],
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _httpx_client


def load_cookie() -> tuple:
    """Load the gemini-auth.json cookie file (JSON only) with mtime-based caching."""
    cookie_file = CONFIG.get("cookie_file")
    if not cookie_file or not (cookie_path := Path(cookie_file)).exists():
        return "", None
    try:
        mtime = cookie_path.stat().st_mtime
        if mtime == _cookie_cache["mtime"] and _cookie_cache["str"]:
            return _cookie_cache["str"], _cookie_cache["sapisid"]
        data = json.loads(cookie_path.read_text())
        cookie_str = data.get("cookie", "")
        sapisid = data.get("sapisid", "")
        # Extension exports carry fresh page tokens; they override config.json.
        if data.get("xsrf_token"):
            CONFIG["xsrf_token"] = data["xsrf_token"]
        if "auth_user" in data:
            CONFIG["auth_user"] = data["auth_user"]
        _cookie_cache.update({"str": cookie_str, "sapisid": sapisid or None, "mtime": mtime})
        return cookie_str, sapisid if sapisid else None
    except Exception as e:
        log(f"Cookie load error (expected gemini-auth.json JSON): {e}")
        return _cookie_cache["str"], _cookie_cache["sapisid"]


def make_sapisidhash(sapisid: str) -> str:
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"


def _account_prefix() -> str:
    """Return the Gemini account path prefix for non-default Google accounts."""
    auth_user = CONFIG.get("auth_user")
    if auth_user is None or auth_user == "":
        return ""
    return f"/u/{auth_user}"


def auth_headers() -> dict:
    """Cookie and SAPISIDHASH headers from the cookie file; empty when anonymous."""
    cookie_str, sapisid = load_cookie()
    headers = {}
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def _build_headers() -> dict:
    account_prefix = _account_prefix()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": f"https://gemini.google.com{account_prefix}/app",
        "X-Same-Domain": "1",
        **auth_headers(),
    }
    if account_prefix:
        headers["X-Goog-AuthUser"] = str(CONFIG["auth_user"])
    return headers


def _apply_chat_persistence_flags(inner: list) -> None:
    """Apply Gemini Web persistence flags to an outgoing request payload."""
    if CONFIG.get("temporary_chats", False):
        # Match Gemini Web temporary-chat requests.
        inner[41] = [1]
        inner[45] = 1
    else:
        inner[41] = [2]


def _build_payload(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    inner = [None] * 102
    if file_refs:
        refs = [
            [[ref, 1, None, mime, str(uuid.uuid4())], "image." + mime.split("/")[-1]]
            for ref, mime in file_refs
        ]
        inner[0] = [prompt, 0, None, refs, None, None, 0]
    else:
        inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    _apply_chat_persistence_flags(inner)
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if extra_fields:
        for k, v in extra_fields.items():
            inner[k] = v
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    if CONFIG.get("xsrf_token"):
        params["at"] = CONFIG["xsrf_token"]
    return urllib.parse.urlencode(params)


def fetch_gemini_page(timeout: float) -> str:
    """HTML of the Gemini app page (build label and upload tokens live in it)."""
    resp = _get_httpx_client().get("https://gemini.google.com/app", headers=auth_headers(), timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_latest_bl():
    """Fetch the current gemini_bl (build label) from the Gemini page, or None."""
    try:
        html = fetch_gemini_page(timeout=15)
    except Exception as e:
        log(f"BL auto-update fetch failed: {e}")
        return None
    m = re.search(r'(boq_assistant-bard-web-server_\d+\.\d+_p\d+)', html)
    return m.group(1) if m else None


def update_bl_if_needed() -> bool:
    """Refresh gemini_bl from the Gemini page. Returns True if it changed."""
    new_bl = fetch_latest_bl()
    if new_bl and new_bl != CONFIG["gemini_bl"]:
        log(f"BL auto-updated: {CONFIG['gemini_bl']} -> {new_bl}")
        CONFIG["gemini_bl"] = new_bl
        return True
    return False


def _is_stale_bl_error(e: Exception) -> bool:
    """Gemini answers HTTP 405 when gemini_bl is outdated."""
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 405


def _get_url() -> str:
    reqid = int(time.time()) % 1000000
    account_prefix = _account_prefix()
    return (
        f"https://gemini.google.com{account_prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
    )


_CITE_RE = re.compile(r'\[cite(?::\s*\d+(?:\s*,\s*\d+)*)?\]')
# Trailing prefix of a citation marker ("[", "[ci", "[cite: 1") that a later chunk may complete.
_PARTIAL_CITE_RE = re.compile(r'\[(?:c(?:i(?:t(?:e(?::[\d,\s]*)?)?)?)?)?\Z')


def clean_text(text: str, strip: bool = True, strip_citations: bool = False) -> str:
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL
    )
    text = re.sub(r'http://googleusercontent\.com/card_content/\d+\n?', '', text)
    if strip_citations:
        # Gemini tags answers about attached files with [cite: N] markers.
        text = _CITE_RE.sub('', text)
    return text.strip() if strip else text


def _extract_texts_from_line(line: str) -> list:
    """Parse a single wrb.fr line and return list of text strings found."""
    if '"wrb.fr"' not in line or len(line) < 200:
        return []
    try:
        arr = json.loads(line)
        inner_str = arr[0][2]
        if not inner_str or len(inner_str) < 50:
            return []
        inner = json.loads(inner_str)
        if not (isinstance(inner, list) and len(inner) > 4 and inner[4]):
            return []
        texts = []
        for part in inner[4]:
            if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                for t in part[1]:
                    if isinstance(t, str) and t:
                        texts.append(t)
        return texts
    except (json.JSONDecodeError, IndexError, TypeError):
        return []


def extract_response_text(raw: str, strip_citations: bool = False) -> str:
    """Parse full response to get final text."""
    bard_err = re.search(r'BardErrorInfo"?\s*,?\s*\[(\d+)\]', raw)
    if bard_err:
        raise RuntimeError(f"Gemini upstream rejected request: BardErrorInfo [{bard_err.group(1)}]")
    last_text = ""
    for line in raw.split("\n"):
        for t in _extract_texts_from_line(line):
            if len(t) > len(last_text):
                last_text = t
    return clean_text(last_text, strip_citations=strip_citations)


def generate(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None) -> str:
    """Non-streaming generation with retry."""
    load_cookie()  # refresh tokens from cookie file before building the request
    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields)
    url = _get_url()
    headers = _build_headers()
    client = _get_httpx_client()

    last_err = None
    started = time.monotonic()
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            resp = client.post(url, content=body, headers=headers)
            resp.raise_for_status()
            text = extract_response_text(resp.text, strip_citations=bool(file_refs))
            log(f"Gemini responded in {time.monotonic() - started:.1f}s (attempt {attempt+1})")
            return text
        except Exception as e:
            last_err = e
            if _is_stale_bl_error(e) and update_bl_if_needed():
                url = _get_url()
                log("Retrying with updated BL...")
                continue
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Retry {attempt+1}/{CONFIG['retry_attempts']} after {time.monotonic() - started:.1f}s: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def generate_stream(prompt: str, model_id: int, think_mode: int, file_refs: list = None, extra_fields: dict = None):
    """Streaming generation via httpx with retry on connection failure."""
    load_cookie()  # refresh tokens from cookie file before building the request
    body = _build_payload(prompt, model_id, think_mode, file_refs, extra_fields)
    url = _get_url()
    headers = _build_headers()
    client = _get_httpx_client()

    last_err = None
    emitted_raw_text = ""
    latest_raw_text = ""
    started = time.monotonic()
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            with client.stream("POST", url, content=body, headers=headers) as resp:
                resp.raise_for_status()
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    if "BardErrorInfo" in buf:
                        bard_err = re.search(r'BardErrorInfo"?\s*,?\s*\[(\d+)\]', buf)
                        if bard_err:
                            raise RuntimeError(
                                f"Gemini upstream rejected request: BardErrorInfo [{bard_err.group(1)}]"
                            )
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        for t in _extract_texts_from_line(line):
                            if t == emitted_raw_text or emitted_raw_text.startswith(t):
                                continue
                            if not t.startswith(emitted_raw_text):
                                raise RuntimeError("Gemini stream content changed during retry")
                            latest_raw_text = t
                            partial = _PARTIAL_CITE_RE.search(t) if file_refs else None
                            safe = t[:partial.start()] if partial else t
                            if len(safe) <= len(emitted_raw_text):
                                continue
                            delta = clean_text(safe[len(emitted_raw_text):], strip=False, strip_citations=bool(file_refs))
                            emitted_raw_text = safe
                            if delta:
                                yield delta
            if len(latest_raw_text) > len(emitted_raw_text):
                delta = clean_text(latest_raw_text[len(emitted_raw_text):], strip=False, strip_citations=bool(file_refs))
                emitted_raw_text = latest_raw_text
                if delta:
                    yield delta
            log(f"Gemini stream finished in {time.monotonic() - started:.1f}s (attempt {attempt+1})")
            return
        except Exception as e:
            last_err = e
            if _is_stale_bl_error(e) and update_bl_if_needed():
                url = _get_url()
                log("Retrying with updated BL...")
                continue
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Stream retry {attempt+1}/{CONFIG['retry_attempts']} after {time.monotonic() - started:.1f}s: {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err
