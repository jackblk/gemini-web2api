# gemini-web2api

<p align="center">
  <img src="logo.png" width="200" alt="gemini-web2api logo">
</p>

> **About this fork**
>
> - **Refactored**: httpx only, cleaner code, better logging, single codebase.
> - **Bug fixes**: image upload, relative cookie file path.
> - **Features**: request IDs and timings in logs, `gemini_bl` auto-update, one `gemini-auth.json` file for all auth values, with hot reload.

Convert Google Gemini's web interface into an OpenAI-compatible API. Zero cost, cross-platform, single file.

## Features

- **Optional API Keys**: no auth when `api_keys` is empty, OpenAI-style Bearer auth when configured
- **OpenAI Compatible**: Drop-in replacement for `/v1/chat/completions` and `/v1/models`
- **Tool Calling**: Full function calling support (OpenAI format)
- **Multiple Models**: Flash (3.6), Extended Thinking (20k+ char output), Pro, Auto, Lite
- **Thinking Depth**: Adjustable via `@think=N` suffix (0=deepest, 4=shallowest)
- **Web Search**: Built-in internet access (Gemini's native search)
- **Cross-Platform**: Pure Python, one dependency (`httpx`)
- **Streaming**: SSE streaming support via `httpx`
- **Codex CLI**: Responses API (`/v1/responses`) for OpenAI Codex integration
- **Gemini CLI**: Google native API (`/v1beta/models`) for Gemini CLI compatibility

## Quick Start

```bash
docker run -d --name gemini-web2api -p 8081:8081 ghcr.io/jackblk/gemini-web2api:latest
```

Server starts at `http://localhost:8081/v1`, anonymous and without API keys. See [Docker](#docker)
to use your own `config.json` or sign in.

Without Docker:

```bash
pip install httpx
python gemini_web2api.py
```

If no `config.json` is found, the server creates one with the defaults on first run.

## Client Configuration

### Cherry Studio / ChatBox / any OpenAI client

| Field | Value |
|-------|-------|
| Base URL | `http://localhost:8081/v1` |
| API Key | any `api_keys` value from `config.json`; anything if not configured |
| Model | `gemini-auto` |

### curl

#### bash / macOS / Linux

```bash
curl http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-key" \
  -d '{"model":"gemini-3.5-flash","messages":[{"role":"user","content":"Hello!"}]}'
```

#### PowerShell (Windows)

```powershell
curl.exe --% http://127.0.0.1:8081/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer sk-your-key" -d "{\"model\":\"gemini-3.5-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello!\"}]}"
```

> Note: On Windows PowerShell, use `curl.exe` and `--%` so PowerShell does not reinterpret JSON quoting or curl options.

### OpenAI Python SDK

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8081/v1", api_key="sk-your-key")
resp = client.chat.completions.create(
    model="gemini-3.5-flash-thinking",
    messages=[{"role": "user", "content": "Explain quantum computing"}]
)
print(resp.choices[0].message.content)
```

### Gemini CLI

```bash
export GEMINI_API_KEY=none
export GOOGLE_GEMINI_BASE_URL=http://localhost:8081
gemini
```

Supports Google native API endpoints:
- `GET /v1beta/models` — list models
- `POST /v1beta/models/{model}:generateContent` — non-streaming
- `POST /v1beta/models/{model}:streamGenerateContent` — streaming (SSE)

## Supported Models

> **Model selection is best-effort.** Gemini Web picks the model. Anonymous requests always get
> 3.5 Flash-Lite. Signed in, `gemini-3.6-flash`, `gemini-3.1-pro` and `gemini-flash-lite` get those
> models; other names get the account default. Results depend on your account and on Google's
> updates. `gemini-auto` works best at the moment.

The proxy sends a mode (fast, thinking, pro, auto, lite) and Gemini decides which model answers.
The lists below show what actually answered for each proxy model. Refresh them with
`python scripts/check_models.py --update-readme` (anonymous) and
`python scripts/check_models.py --cookie-file gemini-auth.json --update-readme` (signed in).

### Anonymous

<!-- models:anonymous:start -->
Checked 2026-09-28 with `scripts/check_models.py`.

Works:

- `gemini-auto` (3.5 Flash-Lite)
- `gemini-flash-lite` (3.5 Flash-Lite)

Rerouted to 3.5 Flash-Lite:

- `gemini-3.7-flash`
- `gemini-3.6-flash`
- `gemini-3.5-flash`
- `gemini-3.5-flash-thinking`
- `gemini-3.1-pro`
- `gemini-3.1-pro-enhanced`
- `gemini-3.5-flash-thinking-lite`
<!-- models:anonymous:end -->

### Signed in

Depends on the account's plan. Checked with a free Google account; a paid plan may get other models.

<!-- models:signed-in:start -->
Checked 2026-09-28 with `scripts/check_models.py`.

Works:

- `gemini-3.6-flash` (3.6 Flash)
- `gemini-3.1-pro` (3.1 Pro)
- `gemini-3.1-pro-enhanced` (3.1 Pro)
- `gemini-auto` (3.6 Flash)
- `gemini-flash-lite` (3.5 Flash-Lite)

Rerouted to 3.6 Flash:

- `gemini-3.7-flash`
- `gemini-3.5-flash`
- `gemini-3.5-flash-thinking`
- `gemini-3.5-flash-thinking-lite`
<!-- models:signed-in:end -->

### Thinking Depth

Append `@think=N` to any model name:

```
gemini-3.5-flash-thinking@think=0   # deepest (default)
gemini-3.5-flash-thinking@think=2   # medium
gemini-3.5-flash-thinking@think=4   # shallowest
```

## Authentication

### Anonymous (default)

No setup needed. Leave `cookie_file` as `null`. Some models do not work anonymously and are
rerouted; see [Supported Models](#supported-models).

### Signed in

Signing in gets better models than anonymous use: 3.6 Flash by default with a free account, and
3.1 Pro or 3.5 Flash-Lite with `gemini-3.1-pro` or `gemini-flash-lite`. Which models answer depends
on the account's plan; see [Supported Models](#supported-models).

1. Create `gemini-auth.json`, either:
   - **with the extension (recommended)**: install and run the cookie-sync extension as described in
     [gemini-cookie-sync-extension/README.txt](gemini-cookie-sync-extension/README.txt); it exports
     `gemini-auth.json` (details in [SETUP.md](gemini-cookie-sync-extension/SETUP.md)), or
   - **by hand**, with this format:

     ```json
     {
       "cookie": "SID=xxx; HSID=xxx; SSID=xxx; APISID=xxx; SAPISID=xxx; __Secure-1PSID=xxx",
       "sapisid": "value of the SAPISID cookie",
       "auth_user": null,
       "xsrf_token": "AOOh0P..."
     }
     ```

     Cookie values come from DevTools (F12) → Application → Cookies → `https://gemini.google.com`.
     `auth_user` is the `N` in a `https://gemini.google.com/u/N/app` URL (`null` for the default
     account). `xsrf_token` is the `SNlM0e` value in the page source.

2. Point `config.json` at it (a relative path resolves against the folder of `config.json`):

   ```json
   {"cookie_file": "./gemini-auth.json"}
   ```

   Or pass `--cookie-file gemini-auth.json` on the command line.

`auth_user` and `xsrf_token` in `gemini-auth.json` override `config.json`. To refresh,
replace the file; the server reloads it on the next request without a restart.

If requests return HTTP 400 with an `xsrf` error, export `gemini-auth.json` again and make sure
`auth_user` matches the `/u/<index>/` part of the browser URL.

## Configuration

Create `config.json` in the same directory:

```json
{
  "port": 8081,
  "host": "0.0.0.0",
  "retry_attempts": 3,
  "retry_delay_sec": 2,
  "request_timeout_sec": 180,
  "api_keys": ["sk-your-key"],
  "cookie_file": null,
  "proxy": null,
  "log_requests": true,
  "temporary_chats": false
}
```

Set `temporary_chats` to `true` to use Gemini Web temporary chats instead of
persisting conversations to the account history.

When `api_keys` is `[]`, authentication is disabled. When one or more keys are set, `/v1/*` endpoints require `Authorization: Bearer <key>` or `x-api-key: <key>`.

## Docker

Use the published image `ghcr.io/jackblk/gemini-web2api`

```bash
# create config file
cp config.example.json config.json
# run container with the config file
docker run -d \
  --name gemini-web2api \
  -p 8081:8081 \
  -v ./config.json:/app/config.json \
  ghcr.io/jackblk/gemini-web2api:latest
```

Or with Docker Compose (`docker-compose.yml`):

```yaml
services:
  gemini-web2api:
    image: ghcr.io/jackblk/gemini-web2api:latest
    container_name: gemini-web2api
    ports:
      - "8081:8081"
    volumes:
      - ./config.json:/app/config.json
      # - ./gemini-auth.json:/app/gemini-auth.json  # to sign in
    restart: unless-stopped
```

```bash
docker compose up -d
```

To sign in, also mount `gemini-auth.json`:

```bash
docker run -d --name gemini-web2api -p 8081:8081 -v ./config.json:/app/config.json -v ./gemini-auth.json:/app/gemini-auth.json ghcr.io/jackblk/gemini-web2api:latest
```

Set `"cookie_file": "/app/gemini-auth.json"` in `config.json`.

> **Note**: If you get empty responses (`content: null`) with Docker's default bridge network, switch to host networking: `docker run --network host ...` or add `network_mode: host` in your compose file. This is caused by Gemini's upstream rejecting requests from certain Docker NAT IP ranges.

## Proxy

If you cannot access `gemini.google.com` directly (connection timeout), configure a proxy:

**Method 1: CLI argument**
```bash
python gemini_web2api.py --proxy http://127.0.0.1:7890
```

**Method 2: config.json**
```json
{"proxy": "http://127.0.0.1:7890"}
```

**Method 3: Environment variable** (auto-detected)
```bash
export HTTPS_PROXY=http://127.0.0.1:7890
python gemini_web2api.py
```

Works with Clash, V2Ray, Shadowsocks, or any HTTP proxy.

## Tool Calling

```python
resp = client.chat.completions.create(
    model="gemini-3.5-flash",
    messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
        }
    }]
)
```

## Image Input

OpenAI-style multimodal messages are supported for Chat Completions and the
Responses API. Use either HTTP(S) image URLs or base64 data URLs:

```python
resp = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image"},
            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}
        ]
    }]
)
```

## Limitations

- **Image upload may require cookies**: Multimodal input uses Gemini Web's image upload endpoint. If anonymous upload fails, configure a Gemini cookie.
- **Best-effort model selection**: the requested model name does not decide which model answers; see [Supported Models](#supported-models).
- **Single-turn only**: Each request is an independent conversation. Multi-turn context is simulated by including previous messages in the prompt.
- **Rate limits**: Google may throttle high-frequency requests. The server retries automatically but sustained heavy use may be blocked.

## Requirements

- Python 3.8+
- `httpx` (`pip install httpx`) — required, used for all upstream requests
- Network access to `gemini.google.com` (proxy/VPN may be needed in some regions)

## How It Works

This tool reverse-engineers Google Gemini's web StreamGenerate protocol. It sends requests to the same endpoint that the Gemini web app uses, converting between OpenAI's API format and Gemini's internal protobuf-like format.

The model selection is controlled by field `[79]` in the request payload, mapped from Gemini's frontend JavaScript source (`MODE_CATEGORY` enum).

## Acknowledgments

- Upstream: [Sophomoresty/gemini-web2api](https://github.com/Sophomoresty/gemini-web2api)
- Inspired by the open-source API proxy ecosystem

## License

MIT
