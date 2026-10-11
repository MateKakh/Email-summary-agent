# Email Summary Agent

A small Python service that emails you a daily digest of the last 24 hours
of your Gmail, with an LLM-generated summary and a list of things you
actually need to act on.

This is a standalone Python port of an n8n workflow. It runs anywhere
Python runs — no n8n required.

## What it does

Every day at 07:00 (configurable):

1. Pulls every Gmail message that mentions your configured address and
   arrived in the last 24 hours.
2. Extracts `id`, `From`, `To`, `Cc`, and `snippet` for each one.
3. Asks an OpenAI model to produce a JSON summary with two lists:
   - `summary_of_emails` — one bullet per email/thread.
   - `actions` — things you need to do (reply, approve, pay, follow up).
4. Renders the summary into an HTML email and sends it to your recipients.

## Requirements

- Python 3.9+
- A Google account with the Gmail API enabled
- An OpenAI API key

## Install

```bash
git clone https://github.com/<you>/email-summary-agent.git
cd email-summary-agent

python -m venv venv
source venv/bin/activate       # or venv\Scripts\activate on Windows

pip install google-auth google-auth-oauthlib google-auth-httplib2 \
            google-api-python-client schedule openai
```

## Configure

All settings can be passed as environment variables. Anything you don't
set falls back to a default baked into the script.

| Variable | Purpose | Default |
|---|---|---|
| `GMAIL_CLIENT_ID` | OAuth 2.0 client ID | required |
| `GMAIL_CLIENT_SECRET` | OAuth 2.0 client secret | required |
| `GMAIL_REDIRECT_URI` | Must match the URI registered in Google Cloud | `http://localhost:8080/` |
| `OPENAI_API_KEY` | OpenAI key | required |
| `OPENAI_MODEL` | Model to use | `gpt-4o-mini` |
| `SOURCE_EMAIL` | Address to search for in Gmail | `isb.quantana@quantana.in` |
| `SEND_TO` | Recipient of the summary email | `user@example.com` |
| `CC_LIST` | Optional CC list (empty = none) | *(empty)* |
| `RUN_HOUR` | Hour to run, 24h format | `7` |
| `RUN_MINUTE` | Minute to run | `0` |

You can also just edit the constants at the top of `email_summary_agent.py`.
Environment variables always win.

## Google Cloud setup

You need OAuth credentials so the script can read and send mail as you.

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and
   create (or pick) a project.
2. Enable the **Gmail API** under *APIs & Services → Library*.
3. Configure the OAuth consent screen (*APIs & Services → OAuth consent
   screen*). Pick **External** unless you're on Workspace. Add the two
   scopes below and add your own Gmail address as a test user:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
4. Under *APIs & Services → Credentials*, create an **OAuth client ID**:
   - Application type: **Desktop app**
   - Under **Authorized redirect URIs**, add `http://localhost:8080/`
5. Copy the client ID and secret into your environment.

Note: while the consent screen is in "Testing", refresh tokens expire after
seven days. Publish the app (or use an internal Workspace app) to avoid
re-authorizing every week.

## First run

The first run opens a browser to complete the OAuth dance, then writes a
`token.json` next to the script. Later runs reuse it silently.

```bash
python email_summary_agent.py --once
```

On a headless server, run this step on your laptop and copy `token.json`
to the server. Delete `token.json` to force re-authentication.

## Running

Once:

```bash
python email_summary_agent.py --once
```

With the built-in scheduler (keeps the process alive and fires daily):

```bash
python email_summary_agent.py
```

For anything long-lived, prefer cron or a systemd timer.

Cron:

```
0 7 * * * cd /opt/email-summary-agent && ./venv/bin/python email_summary_agent.py --once >> /var/log/esagent.log 2>&1
```

systemd timer:

```ini
# /etc/systemd/system/email-summary-agent.service
[Unit]
Description=Email Summary Agent
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/email-summary-agent
ExecStart=/opt/email-summary-agent/venv/bin/python email_summary_agent.py --once
User=youruser
```

```ini
# /etc/systemd/system/email-summary-agent.timer
[Unit]
Description=Run Email Summary Agent daily at 07:00

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now email-summary-agent.timer
```

## Tweaking things

**Different LLM.** Replace the OpenAI block in `summarize()` with your
provider's SDK. Keep the prompt and the JSON schema the same — the rest of
the code expects `summary_of_emails` and `actions`.

**Different template.** Edit the `HTML` string. The two placeholders,
`{summaries}` and `{actions}`, are filled in by `render()`.

**Different search.** `fetch_messages()` builds the Gmail query. Add
`is:unread`, exclude promotions, etc. as needed.

## Troubleshooting

- `Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET before running` — you
  haven't provided credentials yet.
- `Token refresh failed` / 403 access_denied — refresh token expired or
  your address isn't a test user on the consent screen. Delete
  `token.json` and run again.
- No emails found — check `SOURCE_EMAIL` and whether there really was
  anything in the last 24h.
- Scheduler doesn't fire — the process probably isn't running. Use cron
  or a systemd timer instead.

Logs go to stdout. When running under cron or systemd, redirect them to a
file.

## Security

- Don't commit `token.json`, your client secret, or your OpenAI key.
- `token.json` grants access to your Gmail — `chmod 600` it.
- The requested scopes are read-only plus send. The script cannot delete
  or modify mail.
- If you're rolling this out to a team, use a dedicated account or a
  Workspace service account rather than your personal one.

## Layout

```
email-summary-agent/
├── email_summary_agent.py
├── README.md
└── token.json          # created on first run; gitignore this
```

## License

MIT.
