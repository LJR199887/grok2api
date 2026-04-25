import asyncio

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountPatch, AccountUpsert
from app.control.account.enums import AccountStatus


def test_upsert_preserves_disabled_account_by_default(tmp_path):
    async def run():
        repo = LocalAccountRepository(tmp_path / "accounts.db")
        await repo.initialize()
        await repo.upsert_accounts([AccountUpsert(token="tok-disabled", pool="super")])
        await repo.patch_accounts([
            AccountPatch(
                token="tok-disabled",
                status=AccountStatus.DISABLED,
                state_reason="operator_disabled",
                ext_merge={"disabled_reason": "operator_disabled"},
            )
        ])

        await repo.upsert_accounts([AccountUpsert(token="tok-disabled", pool="basic")])
        record = (await repo.get_accounts(["tok-disabled"]))[0]

        assert record.status == AccountStatus.DISABLED
        assert record.pool == "basic"
        assert record.state_reason == "operator_disabled"
        assert record.ext["disabled_reason"] == "operator_disabled"

    asyncio.run(run())


def test_upsert_can_reactivate_when_explicitly_allowed(tmp_path):
    async def run():
        repo = LocalAccountRepository(tmp_path / "accounts.db")
        await repo.initialize()
        await repo.upsert_accounts([AccountUpsert(token="tok-reactivate", pool="super")])
        await repo.patch_accounts([
            AccountPatch(
                token="tok-reactivate",
                status=AccountStatus.DISABLED,
                state_reason="operator_disabled",
                ext_merge={"disabled_reason": "operator_disabled"},
            )
        ])

        await repo.upsert_accounts([
            AccountUpsert(
                token="tok-reactivate",
                pool="basic",
                allow_reactivate=True,
            )
        ])
        record = (await repo.get_accounts(["tok-reactivate"]))[0]

        assert record.status == AccountStatus.ACTIVE
        assert record.pool == "basic"
        assert record.state_reason is None
        assert "disabled_reason" not in record.ext

    asyncio.run(run())
