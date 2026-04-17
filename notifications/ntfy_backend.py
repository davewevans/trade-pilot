"""ntfy push notification backend.

Sends HTTP POST to an ntfy.sh topic (or self-hosted server).
Requires NTFY_TOPIC env var; silently drops notifications when unset.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

NTFY_SERVER_DEFAULT = "https://ntfy.sh"
PRIORITY_MAP = {"critical": 5, "warning": 3, "info": 2}


class NtfyBackend:
    def __init__(self):
        self.topic = os.environ.get("NTFY_TOPIC", "").strip()
        self.server = os.environ.get("NTFY_SERVER", NTFY_SERVER_DEFAULT).rstrip("/")

    def send(self, title: str, message: str, tags: list[str], priority: int = 3) -> None:
        """POST a notification to the configured ntfy topic.

        Silently no-ops when NTFY_TOPIC is not set.
        Logs warnings on timeout or HTTP failure, but never raises.
        """
        if not self.topic:
            logger.debug("NTFY_TOPIC not set; notification dropped: %s", title)
            return
        url = f"{self.server}/{self.topic}"
        headers = {
            "X-Title": title,
            "X-Priority": str(priority),
        }
        if tags:
            headers["X-Tags"] = ",".join(tags)
        try:
            resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=5)
            resp.raise_for_status()
        except requests.Timeout:
            logger.warning("ntfy POST timed out for topic %s, title: %s", self.topic, title)
        except Exception as e:
            logger.warning("ntfy POST failed: %s", e)
