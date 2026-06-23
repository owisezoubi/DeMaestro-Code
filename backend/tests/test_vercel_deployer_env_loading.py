import os
from app.services import vercel_deployer


def test_load_dotenv_runs_at_import():
    """load_dotenv should have been called at import time."""
    assert hasattr(vercel_deployer, "load_dotenv")


def test_is_configured_false_when_token_missing(monkeypatch):
    monkeypatch.delenv("DEMAESTRO_VERCEL_TOKEN", raising=False)
    monkeypatch.setenv("DEMAESTRO_POSTGRES_URL", "postgresql://x")
    assert vercel_deployer.is_configured() is False


def test_is_configured_false_when_postgres_missing(monkeypatch):
    monkeypatch.setenv("DEMAESTRO_VERCEL_TOKEN", "vcp_abc")
    monkeypatch.delenv("DEMAESTRO_POSTGRES_URL", raising=False)
    assert vercel_deployer.is_configured() is False


def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setenv("DEMAESTRO_VERCEL_TOKEN", "vcp_abc")
    monkeypatch.setenv("DEMAESTRO_POSTGRES_URL", "postgresql://user:pw@host/db")
    assert vercel_deployer.is_configured() is True


def test_auto_deploy_disabled_when_flag_false(monkeypatch):
    monkeypatch.setenv("DEMAESTRO_VERCEL_TOKEN", "vcp_abc")
    monkeypatch.setenv("DEMAESTRO_POSTGRES_URL", "postgresql://x")
    monkeypatch.setenv("DEMAESTRO_AUTO_DEPLOY", "false")
    assert vercel_deployer.is_auto_deploy_enabled() is False


def test_auto_deploy_enabled_by_default_when_configured(monkeypatch):
    monkeypatch.setenv("DEMAESTRO_VERCEL_TOKEN", "vcp_abc")
    monkeypatch.setenv("DEMAESTRO_POSTGRES_URL", "postgresql://x")
    monkeypatch.delenv("DEMAESTRO_AUTO_DEPLOY", raising=False)
    assert vercel_deployer.is_auto_deploy_enabled() is True
