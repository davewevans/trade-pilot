"""Account configuration management.

Loads, saves, and queries account config from a JSON file on disk.
If the config file doesn't exist in the data dir, falls back to the
repo default at data/account_config.json and copies it to the data dir.
"""

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REPO_DEFAULT = Path(__file__).resolve().parent / "account_config.json"


def _config_path() -> Path:
    from config import settings
    return settings.DATA_DIR / "account_config.json"


class AccountManager:
    """Manages per-account configuration stored in account_config.json."""

    def __init__(self, config_path: Optional[Path] = None):
        self._path = Path(config_path) if config_path else _config_path()
        self._data: dict = self._load()

    # ── loading / saving ──────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not self._path.exists():
            if _REPO_DEFAULT.exists():
                logger.info(
                    "AccountManager: config not found at %s — copying repo default",
                    self._path,
                )
                self._path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(_REPO_DEFAULT, self._path)
            else:
                logger.warning(
                    "AccountManager: no config found at %s or repo default — starting empty",
                    self._path,
                )
                return {"accounts": {}, "updated_at": None}

        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("AccountManager: failed to load config from %s", self._path)
            return {"accounts": {}, "updated_at": None}

    def _save(self) -> None:
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("AccountManager: failed to save config to %s", self._path)

    # ── public query API ──────────────────────────────────────────────────────

    def get_all_accounts(self) -> dict[str, dict]:
        """Return all accounts as a dict keyed by account_id."""
        return dict(self._data.get("accounts", {}))

    def get_account(self, account_id: str) -> Optional[dict]:
        """Return account config for account_id, or None if not found."""
        return self._data.get("accounts", {}).get(account_id)

    def get_active_accounts(self) -> dict[str, dict]:
        """Return only accounts with status == 'active'."""
        return {
            k: v
            for k, v in self._data.get("accounts", {}).items()
            if v.get("status") == "active"
        }

    # ── mutation API ──────────────────────────────────────────────────────────

    def activate(self, account_id: str) -> bool:
        """Set account status to active. Returns False if account not found or no strategy linked."""
        account = self._data.get("accounts", {}).get(account_id)
        if account is None:
            logger.warning("AccountManager.activate: account %r not found", account_id)
            return False
        if not account.get("strategy"):
            logger.warning(
                "AccountManager.activate: account %r has no strategy linked", account_id
            )
            return False
        account["status"] = "active"
        self._save()
        return True

    def deactivate(self, account_id: str) -> bool:
        """Set account status to inactive. Returns False if account not found."""
        account = self._data.get("accounts", {}).get(account_id)
        if account is None:
            logger.warning("AccountManager.deactivate: account %r not found", account_id)
            return False
        account["status"] = "inactive"
        self._save()
        return True

    def link_strategy(self, account_id: str, strategy_name: str) -> tuple[bool, str]:
        """Link a strategy to an account (one-time operation).

        Returns (True, "") on success.
        Returns (False, error_message) if already linked or strategy doesn't exist.
        """
        account = self._data.get("accounts", {}).get(account_id)
        if account is None:
            return False, f"Account {account_id!r} not found"

        if account.get("strategy"):
            return False, (
                f"Account {account_id!r} already has strategy "
                f"{account['strategy']!r} linked. Cannot change."
            )

        # Validate strategy definition exists
        try:
            from strategies.strategy_loader import load_strategy
            load_strategy(strategy_name)
        except FileNotFoundError:
            return False, f"Strategy definition {strategy_name!r} does not exist"
        except Exception as e:
            return False, f"Failed to validate strategy {strategy_name!r}: {e}"

        account["strategy"] = strategy_name
        self._save()
        return True, ""

    def update_watchlist(self, account_id: str, watchlist: list[str]) -> bool:
        """Update per-account watchlist. Uppercases all symbols. Returns False if not found."""
        account = self._data.get("accounts", {}).get(account_id)
        if account is None:
            return False
        account["watchlist"] = [s.upper() for s in watchlist]
        self._save()
        return True

    def update_screening_overrides(self, account_id: str, overrides: dict) -> bool:
        """Update per-account screening overrides. Returns False if account not found."""
        account = self._data.get("accounts", {}).get(account_id)
        if account is None:
            return False
        account["screening_overrides"] = overrides
        self._save()
        return True

    def get_credentials(self, account_id: str) -> tuple[str, str]:
        """Return (api_key, secret_key) by reading env vars named in the config.

        Raises ValueError if account not found or credentials not set.
        """
        account = self._data.get("accounts", {}).get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id!r} not found")

        key_var = account.get("credentials_key", "")
        secret_var = account.get("credentials_secret", "")

        api_key = os.getenv(key_var, "") if key_var else ""
        secret_key = os.getenv(secret_var, "") if secret_var else ""

        if not api_key or not secret_key:
            raise ValueError(
                f"Credentials for account {account_id!r} not set. "
                f"Expected env vars: {key_var}, {secret_var}"
            )
        return api_key, secret_key
