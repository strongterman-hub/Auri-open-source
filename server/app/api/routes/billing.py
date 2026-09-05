from __future__ import annotations

from urllib.parse import parse_qsl

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.billing.models import (
    CreatePaymentOrderRequest,
    CreditsBalanceResponse,
    PaymentOrderResponse,
)


router = APIRouter(prefix="/billing", tags=["billing"])


def _order_response(order, order_string: str | None = None) -> PaymentOrderResponse:
    return PaymentOrderResponse(
        order_id=order.order_id,
        amount_yuan=order.amount_yuan,
        credits=order.credits,
        status=order.status,
        order_string=order_string,
    )


@router.get("/balance", response_model=CreditsBalanceResponse)
async def credits_balance(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> CreditsBalanceResponse:
    return CreditsBalanceResponse(
        balance_credits=container.billing_service.balance_text(current_user.id),
        welcome_credits=container.settings.billing_welcome_credits,
        credits_per_yuan=container.settings.billing_credits_per_yuan,
    )


@router.post("/orders", response_model=PaymentOrderResponse)
async def create_payment_order(
    payload: CreatePaymentOrderRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> PaymentOrderResponse:
    order, order_string = container.billing_service.create_order(
        current_user.id, payload.amount_yuan
    )
    return _order_response(order, order_string)


@router.get("/orders/{order_id}", response_model=PaymentOrderResponse)
async def payment_order_status(
    order_id: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
    refresh: bool = Query(default=False),
) -> PaymentOrderResponse:
    order = await container.billing_service.get_order(
        current_user.id, order_id, refresh=refresh
    )
    return _order_response(order)


@router.post("/alipay/notify", include_in_schema=False)
async def alipay_notification(request: Request, container: ContainerDep) -> PlainTextResponse:
    body = (await request.body()).decode("utf-8", errors="replace")
    params = dict(parse_qsl(body, keep_blank_values=True))
    accepted = container.billing_service.handle_alipay_notification(params)
    return PlainTextResponse("success" if accepted else "failure")
