from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cryptography.fernet import Fernet

from app.agent.tools import HealthSyncTool
from app.auth.store import AuthStore
from app.config import Settings
from app.integrations.xiaomi.credential_store import CredentialStore
from app.memory.models import MemoryScope
from app.proactive.models import TriggerType
from app.services.health_sync_scheduler import HealthSyncScheduler
from app.state.container import create_container


class FakeXiaomiService:
    def __init__(self) -> None:
        self.synced: list[str] = []

    async def sync(self, user_id: str) -> dict:
        self.synced.append(user_id)
        return {"synced": 1}


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, TriggerType, str]] = []

    async def evaluate_daily(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
        trigger_source: str = "time",
    ) -> None:
        self.calls.append((user_id, agent_id, trigger_type, trigger_source))


class FakeAgentXiaomiService:
    def __init__(self, *, bound: bool) -> None:
        self.bound = bound
        self.synced: list[str] = []
        self.last_sync_at: int | None = None

    def status(self, user_id: str) -> dict:
        return {
            "bound": self.bound,
            "last_sync_at": self.last_sync_at,
            "available_data_types": ["daily_activity"] if self.bound else [],
        }

    async def sync(self, user_id: str) -> dict:
        self.synced.append(user_id)
        self.last_sync_at = 1788393600000
        return {
            "synced": 3,
            "metrics": 2,
            "samples": 1,
            "sleep_scores": 0,
            "from_day": "2026-08-05",
            "to_day": "2026-09-03",
            "available_data_types": ["daily_activity"],
        }


def test_health_sync_syncs_bound_users(tmp_dir: Path) -> None:
    auth_store = AuthStore(tmp_dir / "auth")
    auth_store.create_user("bound@example.com", "test1234")
    auth_store.create_user("unbound@example.com", "test1234")

    credential_store = CredentialStore(
        tmp_dir / "credentials",
        Fernet.generate_key().decode(),
    )
    credential_store.save("bound@example.com", "mi-1", "pass-1")

    xiaomi = FakeXiaomiService()
    scheduler = HealthSyncScheduler(
        auth_store=auth_store,
        credential_store=credential_store,
        xiaomi_service=xiaomi,
        tick_seconds=60,
    )

    synced = asyncio.run(scheduler.tick())

    assert synced == ["bound@example.com"]
    assert xiaomi.synced == ["bound@example.com"]


def test_health_sync_triggers_proactive_event(tmp_dir: Path) -> None:
    auth_store = AuthStore(tmp_dir / "auth")
    auth_store.create_user("bound@example.com", "test1234")

    credential_store = CredentialStore(
        tmp_dir / "credentials",
        Fernet.generate_key().decode(),
    )
    credential_store.save("bound@example.com", "mi-1", "pass-1")

    engine = FakeEngine()
    scheduler = HealthSyncScheduler(
        auth_store=auth_store,
        credential_store=credential_store,
        xiaomi_service=FakeXiaomiService(),
        tick_seconds=60,
        proactive_engine=engine,
    )

    asyncio.run(scheduler.tick())

    assert engine.calls == [
        ("bound@example.com", "default", TriggerType.event, "health")
    ]


def test_health_sync_tool_refreshes_current_user() -> None:
    xiaomi = FakeAgentXiaomiService(bound=True)
    tool = HealthSyncTool(xiaomi, "current@example.com")

    payload = json.loads(asyncio.run(tool.execute()))

    assert xiaomi.synced == ["current@example.com"]
    assert payload["ok"] is True
    assert payload["synced"] == 3
    assert payload["from_day"] == "2026-08-05"
    assert payload["to_day"] == "2026-09-03"
    assert payload["last_sync_at"] == 1788393600000


def test_health_sync_tool_requires_xiaomi_connection() -> None:
    xiaomi = FakeAgentXiaomiService(bound=False)
    tool = HealthSyncTool(xiaomi, "current@example.com")

    payload = json.loads(asyncio.run(tool.execute()))

    assert xiaomi.synced == []
    assert payload["ok"] is False
    assert payload["bound"] is False
    assert payload["action_required"] == "connect_xiaomi_health"


def test_health_sync_tool_is_exposed_only_to_chat_agent(tmp_dir: Path) -> None:
    container = create_container(
        Settings(
            data_dir=tmp_dir,
            llm_provider="echo",
            event_extraction_enabled=False,
            web_search_provider="none",
        )
    )
    scope = MemoryScope(user_id="current@example.com")

    chat_tool_names = {tool.name for tool in container.agent_service.tool_factory(scope)}
    proactive_tool_names = {
        tool.name for tool in container.proactive_engine.tool_factory(scope)
    }

    assert "sync_health_data" in chat_tool_names
    assert "sync_health_data" not in proactive_tool_names
    assert "get_current_location" in proactive_tool_names
