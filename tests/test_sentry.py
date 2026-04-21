"""Tests verifying Sentry SDK initialisation behaviour.

Tests the init logic in isolation — does not import api.server (which requires
pydantic/FastAPI) so these tests run even if the full server deps are broken.
"""

from unittest.mock import MagicMock, call, patch
import sentry_sdk


def _run_init_block(sentry_dsn: str, render: bool = True, version: str = "1.0.0"):
    """Execute the same init block that api/server.py runs at module level."""
    if sentry_dsn:
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment="production" if render else "development",
            release=version,
            traces_sample_rate=0.1,
            send_default_pii=False,
        )


def test_sentry_init_called_with_correct_kwargs():
    """init() is called with the configured DSN and expected kwargs."""
    dsn = "https://test@sentry.example.com/123"
    with patch.object(sentry_sdk, "init") as mock_init:
        _run_init_block(dsn, render=True, version="2.0.0")

    mock_init.assert_called_once_with(
        dsn=dsn,
        environment="production",
        release="2.0.0",
        traces_sample_rate=0.1,
        send_default_pii=False,
    )


def test_sentry_environment_is_development_when_not_render():
    """environment is 'development' when RENDER flag is False."""
    dsn = "https://test@sentry.example.com/456"
    with patch.object(sentry_sdk, "init") as mock_init:
        _run_init_block(dsn, render=False)

    assert mock_init.call_args.kwargs["environment"] == "development"


def test_sentry_init_skipped_when_dsn_empty():
    """init() is NOT called when SENTRY_DSN is an empty string."""
    with patch.object(sentry_sdk, "init") as mock_init:
        _run_init_block("")

    mock_init.assert_not_called()


def test_config_has_sentry_dsn_setting():
    """config.Settings exposes SENTRY_DSN as a string attribute."""
    from config import Settings
    s = Settings()
    assert hasattr(s, "SENTRY_DSN")
    assert isinstance(s.SENTRY_DSN, str)


def test_config_reads_sentry_dsn_from_env(monkeypatch):
    """SENTRY_DSN env var is picked up by Settings."""
    dsn = "https://abc@sentry.io/999"
    monkeypatch.setenv("SENTRY_DSN", dsn)
    # Force re-instantiation to pick up the new env var
    from config import Settings
    s = Settings()
    assert s.SENTRY_DSN == dsn
