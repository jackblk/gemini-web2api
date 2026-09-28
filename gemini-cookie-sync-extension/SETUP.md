# Gemini Cookie Sync Setup

Short guide for extracting fresh Gemini auth data and applying it to `gemini-web2api`.

## What this extension exports

The extension reads the current signed-in Gemini session and exports:

- Google session cookies
- `SAPISID`
- `SNlM0e` (`xsrf_token`)
- `cfb2h` (`gemini_bl`)
- `auth_user`

It saves them locally as `gemini-auth.json`.

## Install and export

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select the `gemini-cookie-sync-extension` folder
5. Open [https://gemini.google.com/app](https://gemini.google.com/app)
6. Sign in and refresh the page
7. Open the extension and click **Inspect session**
8. Confirm the session looks ready
9. Click **Export gemini-auth.json**

Expected ready state:

```text
XSRF / SNlM0e: present
gemini_bl / cfb2h: present
Session and XSRF are ready for export.
```

## Apply it in `gemini-web2api`

Move the exported file into the project:

```bash
cd /path/to/gemini-web2api

WIN_HOME=$(wslpath "$(powershell.exe -NoProfile -Command '[Environment]::GetFolderPath(\"UserProfile\")' | tr -d '\r')")
cp "$WIN_HOME/Downloads/gemini-auth.json" ./gemini-auth.json
chmod 600 gemini-auth.json
```

Point `config.json` at it once:

```json
{
  "cookie_file": "./gemini-auth.json"
}
```

A relative `cookie_file` is resolved against the folder that contains `config.json`, not the
working directory. With Docker, mount both files into the same folder (for example `/app`).

The server reads `cookie`, `sapisid`, `xsrf_token`, `gemini_bl` and `auth_user` straight from
`gemini-auth.json`. Values there override `config.json` (`auth_user: null` means the default
account), so you can leave those three keys out of `config.json`.

## Refresh

Export again and replace `gemini-auth.json`. The server notices the file changed and uses the new
values on the next request; no restart or `config.json` edit is needed.

## Check and test

The startup log shows which cookie file was loaded, and warns if it is missing:

```text
[INFO] Cookie:    /app/gemini-auth.json
[WARNING] Cookie:    ./gemini-auth.json not found, requests will be anonymous
```

A missing cookie does not cause errors: Gemini still answers, but anonymously, so chats are not
saved to your account.

```bash
curl -sS http://127.0.0.1:10012/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{
    "model": "gemini-3.1-pro",
    "messages": [
      {
        "role": "user",
        "content": "Reply exactly with: authenticated-ok"
      }
    ]
  }' | jq
```

## Keep it secret

`gemini-auth.json` contains a real Google session. Do not share it, print it, or commit it to Git.
