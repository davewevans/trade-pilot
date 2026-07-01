#!/usr/bin/env python3
"""Notify helper for the ticket runner: ntfy push + (optional) email.

Usage:
    python scripts/eval/notify.py "<title>" "<message>"

Reads the same NTFY_TOPIC / NTFY_SERVER env vars the bot uses. Posts to ntfy
directly (NOT via notifications.notify(), which suppresses ntfy on non-Render
machines — and this runs on the local box). Email delivery rides ntfy's built-in
`Email:` header when EVAL_NOTIFY_EMAIL is set — no SMTP/creds needed. If ntfy.sh's
public email proves unreliable, swap this for a Gmail SMTP send.

Never raises: a failed notification must never fail the run.
"""
import os
import sys

import requests

try:  # load NTFY_TOPIC / EVAL_NOTIFY_EMAIL from .env when run as a bare subprocess
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


def main() -> None:
    title = sys.argv[1] if len(sys.argv) > 1 else "trade-pilot run-tickets"
    message = sys.argv[2] if len(sys.argv) > 2 else ""

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set; notification skipped", file=sys.stderr)
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

    headers = {"X-Title": title, "X-Priority": "4", "X-Tags": "robot,trade-pilot"}
    email = os.environ.get("EVAL_NOTIFY_EMAIL", "").strip()
    if email:
        headers["Email"] = email  # ntfy forwards the message to this address

    try:
        resp = requests.post(
            f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=10
        )
        resp.raise_for_status()
        print(f"notified ntfy '{topic}'" + (f" + email {email}" if email else ""))
    except Exception as e:  # noqa: BLE001 — never fail the run over a notification
        print(f"notify failed (non-fatal): {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
