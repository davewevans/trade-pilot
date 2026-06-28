"""Publish report content to the private GitHub reports repo.

The Hermes self-improvement agent (separate VPS) reads trade-pilot's daily
evaluation bundle from ``GITHUB_REPORTS_REPO`` via the GitHub Contents API.
This module is the single reusable commit path.

Hard rule: **publishing is data exhaust and must never crash the caller.**
Every network call is wrapped; on any error we log and return ``False``. A
GitHub outage must never affect trading or stall a scheduler job.
"""

from __future__ import annotations

import base64
import logging

import requests

from config import settings

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
_TIMEOUT = 20
_HEADERS_BASE = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def publish_report(repo_path: str, content: str, message: str | None = None) -> bool:
    """Commit ``content`` to ``GITHUB_REPORTS_REPO`` at ``repo_path`` via the
    GitHub Contents API. Returns ``True`` on success, ``False`` otherwise.

    Never raises. A no-op (returns ``False``) when ``HERMES_PUBLISH_ENABLED``
    is false or the repo/token are unset.

    Dated paths are normally creates (no ``sha``). If the file already exists
    GitHub rejects the create (HTTP 422); we then GET the current ``sha`` and
    retry the PUT with it so same-day re-runs overwrite cleanly.
    """
    if not settings.HERMES_PUBLISH_ENABLED:
        logger.info("publish_report: HERMES_PUBLISH_ENABLED is false — skipping %s", repo_path)
        return False

    repo = settings.GITHUB_REPORTS_REPO
    token = settings.GITHUB_REPORTS_TOKEN
    if not repo or not token:
        logger.info(
            "publish_report: GITHUB_REPORTS_REPO/GITHUB_REPORTS_TOKEN unset — skipping %s",
            repo_path,
        )
        return False

    url = f"{_API_ROOT}/repos/{repo}/contents/{repo_path}"
    headers = {**_HEADERS_BASE, "Authorization": f"Bearer {token}"}
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    commit_message = message or f"Publish {repo_path}"

    body: dict[str, str] = {"message": commit_message, "content": encoded}

    try:
        resp = requests.put(url, headers=headers, json=body, timeout=_TIMEOUT)

        # File already exists — Contents API wants the current blob sha to update.
        if resp.status_code == 422:
            sha = _get_existing_sha(url, headers)
            if sha is None:
                logger.warning(
                    "publish_report: PUT returned 422 for %s but sha lookup failed",
                    repo_path,
                )
                return False
            body["sha"] = sha
            resp = requests.put(url, headers=headers, json=body, timeout=_TIMEOUT)

        if resp.status_code in (200, 201):
            logger.info("publish_report: committed %s (HTTP %d)", repo_path, resp.status_code)
            return True

        logger.warning(
            "publish_report: unexpected status %d for %s: %s",
            resp.status_code, repo_path, resp.text[:300],
        )
        return False
    except Exception:
        logger.warning("publish_report: request failed for %s", repo_path, exc_info=True)
        return False


def _get_existing_sha(url: str, headers: dict[str, str]) -> str | None:
    """GET the current blob ``sha`` for an existing Contents API path.

    Returns ``None`` on any error or if the response has no ``sha``. Never raises.
    """
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("publish_report: sha GET returned %d", resp.status_code)
            return None
        sha = resp.json().get("sha")
        return sha if isinstance(sha, str) and sha else None
    except Exception:
        logger.warning("publish_report: sha GET failed", exc_info=True)
        return None
