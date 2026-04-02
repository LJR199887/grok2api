import asyncio

from app.services.token.manager import (
    BASIC_POOL_NAME,
    SUPER_POOL_NAME,
    SUPER_DEFAULT_QUOTA,
    TokenManager,
)
from app.services.token.models import TokenInfo, TokenStatus
from app.services.token.pool import TokenPool


def test_sync_usage_auto_disables_detected_basic_token(monkeypatch):
    def fake_get_config(key, default=None):
        if key == "token.auto_disable_basic_tokens":
            return True
        if key == "token.consumed_mode_enabled":
            return False
        if key == "token.save_delay_ms":
            return 500
        return default

    async def fake_usage_get(self, token):
        return {
            "remainingTokens": 80,
            "windowSizeSeconds": 14400,
        }

    monkeypatch.setattr("app.services.token.manager.get_config", fake_get_config)
    monkeypatch.setattr(
        "app.services.token.manager.UsageService.get",
        fake_usage_get,
    )

    mgr = TokenManager()
    mgr.pools = {
        SUPER_POOL_NAME: TokenPool(SUPER_POOL_NAME),
        BASIC_POOL_NAME: TokenPool(BASIC_POOL_NAME),
    }
    mgr._schedule_save = lambda: None

    token = TokenInfo(token="test-basic-token", quota=SUPER_DEFAULT_QUOTA)
    mgr.pools[SUPER_POOL_NAME].add(token)

    ok = asyncio.run(mgr.sync_usage("test-basic-token", consume_on_fail=False))

    assert ok is True
    assert mgr.pools[SUPER_POOL_NAME].get("test-basic-token") is None
    assert mgr.pools[BASIC_POOL_NAME].get("test-basic-token") is token
    assert token.status == TokenStatus.DISABLED


def test_sync_usage_keeps_detected_basic_token_active_when_switch_off(monkeypatch):
    def fake_get_config(key, default=None):
        if key == "token.auto_disable_basic_tokens":
            return False
        if key == "token.consumed_mode_enabled":
            return False
        if key == "token.save_delay_ms":
            return 500
        return default

    async def fake_usage_get(self, token):
        return {
            "remainingTokens": 80,
            "windowSizeSeconds": 14400,
        }

    monkeypatch.setattr("app.services.token.manager.get_config", fake_get_config)
    monkeypatch.setattr(
        "app.services.token.manager.UsageService.get",
        fake_usage_get,
    )

    mgr = TokenManager()
    mgr.pools = {
        SUPER_POOL_NAME: TokenPool(SUPER_POOL_NAME),
        BASIC_POOL_NAME: TokenPool(BASIC_POOL_NAME),
    }
    mgr._schedule_save = lambda: None

    token = TokenInfo(token="test-super-token", quota=SUPER_DEFAULT_QUOTA)
    mgr.pools[SUPER_POOL_NAME].add(token)

    ok = asyncio.run(mgr.sync_usage("test-super-token", consume_on_fail=False))

    assert ok is True
    assert mgr.pools[SUPER_POOL_NAME].get("test-super-token") is None
    assert mgr.pools[BASIC_POOL_NAME].get("test-super-token") is token
    assert token.status == TokenStatus.ACTIVE
