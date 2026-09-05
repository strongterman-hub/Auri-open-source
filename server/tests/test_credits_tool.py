from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.agent.llm import LLMClient, LLMResponse, ToolCall
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.agent.tools import CreditsBalanceTool
from app.billing.service import BillingService
from app.billing.store import CREDIT_MICROS, BillingStore
from app.config import Settings
from app.memory.models import MemoryScope
from app.state.container import create_container


def test_balance_is_fresh_and_scoped_without_debits(tmp_dir: Path) -> None:
    store = BillingStore(tmp_dir / "billing.db", welcome_credits=500)
    service = BillingService(store, alipay=None, credits_per_yuan=87)
    store.ensure_welcome_credit("current")
    store.grant_credits("other", 900, reference="other-grant")
    tool = CreditsBalanceTool(service, "current")

    first = json.loads(asyncio.run(tool.execute()))
    assert first["balance_credits"] == "500.00"
    assert first["credits_per_yuan"] == 87
    assert datetime.fromisoformat(first["queried_at"]).tzinfo is not None
    store.apply_usage_debit("current", 1_250_000, reference="test-debit")
    second = json.loads(asyncio.run(tool.execute()))
    assert second["balance_credits"] == "498.75"
    assert store.balance_micros("current") == 498_750_000
    assert store.balance_micros("other") == 1400 * CREDIT_MICROS
    with pytest.raises(ValueError, match="does not accept arguments"):
        asyncio.run(tool.execute(user_id="other"))


@pytest.mark.parametrize("debit", [500, 501])
def test_empty_balance_tool_still_reads_without_paid_call(tmp_dir: Path, debit: int) -> None:
    store = BillingStore(tmp_dir / "billing.db", welcome_credits=500)
    service = BillingService(store, alipay=None, enforcement_enabled=True)
    store.apply_usage_debit("current", debit * CREDIT_MICROS, reference="exhaust")
    result = json.loads(asyncio.run(CreditsBalanceTool(service, "current").execute()))
    assert result["balance_credits"] == "0.00"
    assert result["recharge_entry"] == "账号中心 → Credits"


def test_balance_failure_is_not_converted_to_zero() -> None:
    class UnavailableBilling:
        def balance_text(self, user_id):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(CreditsBalanceTool(UnavailableBilling(), "current").execute())


def test_container_binds_tool_to_each_chat_account(tmp_dir: Path) -> None:
    container = create_container(Settings(
        data_dir=tmp_dir, llm_provider="echo", event_extraction_enabled=False,
        web_search_provider="none",
    ))
    container.billing_store.grant_credits("other", 90, reference="other-grant")
    for user_id, expected in [("current", "500.00"), ("other", "590.00")]:
        scope = MemoryScope(user_id=user_id)
        tool = next(t for t in container.agent_service.tool_factory(scope)
                    if t.name == "get_credits_balance")
        assert json.loads(asyncio.run(tool.execute()))["balance_credits"] == expected
        assert "get_credits_balance" not in {
            t.name for t in container.proactive_engine.tool_factory(scope)
        }


class BalanceQueryLLM(LLMClient):
    async def complete(self, messages, tools=None, max_tokens=None, model=None):
        if messages[-1]["role"] != "tool":
            assert any(t["function"]["name"] == "get_credits_balance" for t in tools)
            return LLMResponse(content="", tool_calls=[
                ToolCall(id="balance-call", name="get_credits_balance", arguments={}),
            ])
        balance = json.loads(messages[-1]["content"])["balance_credits"]
        return LLMResponse(content=f"查询时余额为 {balance} Credits。")


@pytest.mark.parametrize("streaming", [False, True])
def test_runner_returns_real_balance_through_tool_loop(tmp_dir: Path, streaming: bool) -> None:
    service = BillingService(BillingStore(tmp_dir / "billing.db"), alipay=None)
    context = AgentRunContext(
        session_id="test-balance", scope=MemoryScope(user_id="current"),
        history=[{"role": "user", "content": "我的余额还剩多少？"}], memory_prompt="",
        tools=[CreditsBalanceTool(service, "current")],
    )
    runner = BasicAgentRunner(BalanceQueryLLM())

    async def run():
        if not streaming:
            return await runner.run(context)
        async for event in runner.run_stream(context):
            if event.done:
                return event.turn

    turn = asyncio.run(run())
    assert turn.text == "查询时余额为 500.00 Credits。"
    assert turn.tool_results[0]["name"] == "get_credits_balance"
