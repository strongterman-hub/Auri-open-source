from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from app.billing.alipay import AlipayClient
from app.billing.store import CREDIT_MICROS, BillingStore, PaymentOrder
from app.core.errors import AppError, NotFoundError, ServiceUnavailableError, ValidationError
from app.core.pricing import usage_cost


logger = logging.getLogger("auri.billing")


class InsufficientCreditsError(AppError):
    status_code = 402
    code = "insufficient_credits"
    message = "Credits 余额不足，请充值后继续"


class BillingService:
    def __init__(
        self,
        store: BillingStore,
        *,
        alipay: AlipayClient | None,
        credits_per_yuan: int = 95,
        min_recharge_yuan: int = 1,
        max_recharge_yuan: int = 1000,
        enforcement_enabled: bool = False,
    ) -> None:
        self.store = store
        self.alipay = alipay
        self.credits_per_yuan = int(credits_per_yuan)
        self.min_recharge_yuan = int(min_recharge_yuan)
        self.max_recharge_yuan = int(max_recharge_yuan)
        self.enforcement_enabled = bool(enforcement_enabled)

    def balance_text(self, user_id: str) -> str:
        balance = Decimal(self.store.balance_micros(user_id)) / Decimal(CREDIT_MICROS)
        return f"{max(balance, Decimal(0)):.2f}"

    def ensure_available(self, user_id: str) -> None:
        if not self.enforcement_enabled:
            return
        if self.store.balance_micros(user_id) <= 0:
            raise InsufficientCreditsError()

    def ensure_current_user_available(self) -> None:
        from app.core.token_logger import current_token_user_id

        user_id = current_token_user_id()
        if user_id:
            self.ensure_available(user_id)

    def charge_usage(self, record: dict[str, Any]) -> None:
        if not self.enforcement_enabled:
            return
        user_id = record.get("user_id")
        if not user_id:
            return
        cost_yuan = usage_cost(
            str(record.get("model") or ""),
            str(record.get("ts") or ""),
            int(record.get("cached_tokens") or 0),
            int(record.get("uncached_tokens") or 0),
            int(record.get("completion_tokens") or 0),
        )
        amount_micros = int((Decimal(str(cost_yuan)) * Decimal(100) * CREDIT_MICROS).to_integral_value())
        if cost_yuan > 0 and amount_micros == 0:
            amount_micros = 1
        self.store.apply_usage_debit(
            str(user_id),
            amount_micros,
            reference=f"usage:{record['id']}",
            metadata={
                "model": record.get("model"),
                "kind": record.get("kind"),
                "cost_yuan": str(cost_yuan),
            },
        )

    def create_order(self, user_id: str, amount_yuan: int) -> tuple[PaymentOrder, str]:
        if not self.min_recharge_yuan <= amount_yuan <= self.max_recharge_yuan:
            raise ValidationError(
                message=(
                    f"充值金额须为 {self.min_recharge_yuan}–"
                    f"{self.max_recharge_yuan} 元的整数"
                )
            )
        if self.alipay is None:
            raise ServiceUnavailableError(message="支付宝支付尚未配置")
        order = self.store.create_order(user_id, amount_yuan, self.credits_per_yuan)
        order_string = self.alipay.build_order_string(
            order.order_id, order.amount_yuan, order.credits
        )
        return order, order_string

    async def get_order(
        self, user_id: str, order_id: str, *, refresh: bool = False
    ) -> PaymentOrder:
        order = self.store.get_order(order_id, user_id)
        if order is None:
            raise NotFoundError(message="充值订单不存在")
        if refresh and order.status == "pending" and self.alipay is not None:
            try:
                result = await self.alipay.query(order_id)
            except Exception:
                logger.warning("Alipay order query failed order=%s", order_id, exc_info=True)
                return order
            if result.get("trade_status") in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
                paid = self.store.mark_paid(
                    order_id,
                    trade_no=str(result.get("trade_no") or ""),
                    paid_amount_yuan=str(result.get("total_amount") or ""),
                )
                if paid is not None:
                    order = paid
            elif result.get("trade_status") == "TRADE_CLOSED":
                self.store.mark_closed(order_id)
                order = self.store.get_order(order_id, user_id) or order
        return order

    def handle_alipay_notification(self, params: dict[str, str]) -> bool:
        if self.alipay is None or not self.alipay.verify(params):
            return False
        if params.get("app_id") != self.alipay.app_id:
            return False
        order_id = params.get("out_trade_no") or ""
        order = self.store.get_order(order_id)
        if order is None:
            return False
        trade_status = params.get("trade_status")
        if trade_status in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
            return self.store.mark_paid(
                order_id,
                trade_no=params.get("trade_no") or "",
                paid_amount_yuan=params.get("total_amount") or "",
            ) is not None
        if trade_status == "TRADE_CLOSED":
            self.store.mark_closed(order_id)
            return True
        return False
