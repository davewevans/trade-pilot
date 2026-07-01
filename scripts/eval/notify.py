#!/usr/bin/env python3
"""Notify helper for the ticket runner: ntfy push + optional email (SMTP).

Usage:
    python scripts/eval/notify.py "<title>" "<message>"

The two channels are INDEPENDENT — one failing never affects the other or the run,
and neither ever raises.

- ntfy  : NTFY_TOPIC (+ optional NTFY_SERVER). Posted directly (notifications.notify()
          suppresses ntfy on non-Render machines, and this runs locally).
- email : optional. Set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS + EVAL_NOTIFY_EMAIL.
          For Gmail: smtp.gmail.com / 587 / your address / a 16-char App Password.
          If SMTP is unconfigured, email is silently skipped (ntfy still fires).

NOTE: an earlier version rode email on ntfy's `Email:` header — ntfy.sh's public
server rejects that with 400 and it took the whole push down. Hence real SMTP.
"""
import os
import smtplib
import sys
from email.message import EmailMessage

import requests

try:  # load NTFY_/SMTP_/EVAL_NOTIFY_EMAIL from .env when run as a bare subprocess
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


def send_ntfy(title: str, message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set; ntfy skipped", file=sys.stderr)
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    try:
        resp = requests.post(
            f"{server}/{topic}",
            data=message.encode("utf-8"),
            headers={"X-Title": title, "X-Priority": "4", "X-Tags": "robot,trade-pilot"},
            timeout=10,
        )
        resp.raise_for_status()
        print(f"ntfy ok: {topic}")
    except Exception as e:  # noqa: BLE001
        print(f"ntfy failed (non-fatal): {e}", file=sys.stderr)


def send_email(title: str, message: str) -> None:
    to = os.environ.get("EVAL_NOTIFY_EMAIL", "").strip()
    host = os.environ.get("SMTP_HOST", "").strip()
    if not to or not host:
        return  # email not configured — skip quietly
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    pw = os.environ.get("SMTP_PASS", "")
    try:
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = user or to
        msg["To"] = to
        msg.set_content(message)
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            if user:
                s.login(user, pw)
            s.send_message(msg)
        print(f"email ok: {to}")
    except Exception as e:  # noqa: BLE001
        print(f"email failed (non-fatal): {e}", file=sys.stderr)


def main() -> None:
    title = sys.argv[1] if len(sys.argv) > 1 else "trade-pilot run-tickets"
    message = sys.argv[2] if len(sys.argv) > 2 else ""
    send_ntfy(title, message)
    send_email(title, message)


if __name__ == "__main__":
    main()
