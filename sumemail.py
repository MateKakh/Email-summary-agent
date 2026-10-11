"""
Daily email digest bot.

Reads the last 24 hours of Gmail, asks an LLM to summarize everything,
and mails the result to whoever you configure. Designed to be run either
on a schedule inside the process or via cron with `--once`.
"""

import argparse
import base64
import json
import logging
import os
import time
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

import schedule
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from openai import OpenAI


# --- credentials (override with env vars in production) ---------------------
CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "YOUR_CLIENT_ID.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
REDIRECT_URI  = os.environ.get("GMAIL_REDIRECT_URI", "http://localhost:8080/")

OPENAI_KEY   = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

SOURCE_EMAIL = os.environ.get("SOURCE_EMAIL", "isb.quantana@quantana.in")
SEND_TO      = os.environ.get("SEND_TO", "user@example.com")
CC_LIST      = os.environ.get("CC_LIST", "")

RUN_HOUR   = int(os.environ.get("RUN_HOUR", 7))
RUN_MINUTE = int(os.environ.get("RUN_MINUTE", 0))

SCOPES     = ["https://www.googleapis.com/auth/gmail.readonly",
              "https://www.googleapis.com/auth/gmail.send"]
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("esagent")


def gmail_client():
    """Build a Gmail service, running OAuth on the first call."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if "YOUR_CLIENT_ID" in CLIENT_ID:
                raise SystemExit(
                    "Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET before running."
                )
            cfg = {
                "installed": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [REDIRECT_URI],
                }
            }
            flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
            creds = flow.run_local_server(port=urlparse(REDIRECT_URI).port or 8080)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def fetch_messages(service):
    """Return metadata for messages from the source address in the last 24h."""
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y/%m/%d")
    query = f"{SOURCE_EMAIL} after:{yesterday}"
    log.info("searching gmail: %s", query)

    ids, page = [], None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, pageToken=page, maxResults=500
        ).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break

    results = []
    for mid in ids:
        msg = service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "To", "Cc"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        results.append({
            "id":      mid,
            "From":    headers.get("From", ""),
            "To":      headers.get("To", ""),
            "CC":      headers.get("Cc", ""),
            "snippet": msg.get("snippet", ""),
        })
    return results


PROMPT = """You summarize business emails.

Input is a JSON array of {id, From, To, CC, snippet}. Reply with strict JSON
in exactly this shape:

{
  "summary_of_emails": ["bullet", ...],
  "actions": [{"name": "short title", "action": "what to do"}, ...]
}

One bullet per email or thread, mention the sender and the topic. Only list
actions a human actually has to take. No prose outside the JSON."""


def summarize(emails):
    if not OPENAI_KEY:
        raise SystemExit("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=OPENAI_KEY)
    log.info("asking %s to summarize %d message(s)", OPENAI_MODEL, len(emails))

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(emails, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    try:
        data = json.loads(resp.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        log.warning("model returned non-JSON, falling back to empty summary")
        data = {}

    data.setdefault("summary_of_emails", [])
    data.setdefault("actions", [])
    if not isinstance(data["summary_of_emails"], list):
        data["summary_of_emails"] = [str(data["summary_of_emails"])]
    return data


# Email body matches the original n8n template.
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Email Summary</title>
<style>
  body {{ font-family: Arial, sans-serif; background:#f9f9f9; color:#333; margin:0; padding:0; }}
  .wrap  {{ max-width:600px; margin:20px auto; background:#fff; border:1px solid #ddd;
           border-radius:10px; box-shadow:0 2px 5px rgba(0,0,0,.1); }}
  .head  {{ background:#0073e6; color:#fff; padding:20px; text-align:center;
           border-radius:10px 10px 0 0; }}
  .head h1 {{ margin:0; font-size:24px; }}
  .body  {{ padding:20px; }}
  .title {{ font-size:20px; color:#0073e6; margin-bottom:10px; }}
  ul {{ list-style:none; padding:0; }}
  li {{ margin:10px 0; padding:10px; background:#f4f4f4;
        border-left:4px solid #0073e6; border-radius:5px; }}
  .act-name {{ font-weight:bold; }}
  .foot {{ background:#0073e6; color:#fff; text-align:center; padding:10px;
           font-size:14px; border-radius:0 0 10px 10px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="head"><h1>Email Summary</h1></div>
  <div class="body">
    <h2 class="title">Summary of Emails:</h2>
    <ul>{summaries}</ul>
    <h2 class="title">Actions:</h2>
    <ul>{actions}</ul>
  </div>
  <div class="foot">
    <p>Generated by Quantana ESAgent<br>A Quantana AI Labs Initiative</p>
  </div>
</div>
</body>
</html>"""


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(summary):
    items = "".join(f"<li>{esc(x)}</li>" for x in summary["summary_of_emails"])
    if not items:
        items = "<li><em>No emails to summarize.</em></li>"

    acts = "".join(
        f'<li><span class="act-name">{esc(a.get("name",""))}:</span> '
        f'{esc(a.get("action",""))}</li>'
        for a in summary["actions"] if isinstance(a, dict)
    )
    if not acts:
        acts = "<li><em>No actions required.</em></li>"

    return HTML.format(summaries=items, actions=acts)


def send(service, subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["To"] = SEND_TO
    if CC_LIST:
        msg["Cc"] = CC_LIST
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def subject_line():
    y = (date.today() - timedelta(days=1)).strftime("%d %b %Y")
    t = date.today().strftime("%d %b %Y")
    return f"ESAgent - {y}-00:00 to {t}-07:00AM"


def run():
    log.info("---- starting run ----")
    service = gmail_client()
    emails = fetch_messages(service)
    log.info("found %d message(s)", len(emails))

    summary = summarize(emails)
    html = render(summary)

    log.info("sending to %s (cc: %s)", SEND_TO, CC_LIST or "-")
    send(service, subject_line(), html)
    log.info("---- done ----")


def run_safe():
    try:
        run()
    except Exception:
        log.exception("run failed")


def main():
    parser = argparse.ArgumentParser(description="Daily email summary agent")
    parser.add_argument("--once", action="store_true",
                        help="run once and exit (for cron)")
    args = parser.parse_args()

    if args.once:
        run_safe()
        return

    when = f"{RUN_HOUR:02d}:{RUN_MINUTE:02d}"
    schedule.every().day.at(when).do(run_safe)
    log.info("scheduler started, next run at %s local time", when)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()