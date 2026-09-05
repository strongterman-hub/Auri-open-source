from __future__ import annotations

from pydantic import BaseModel, Field


class CreditsBalanceResponse(BaseModel):
    balance_credits: str
    welcome_credits: int
    credits_per_yuan: int


class CreatePaymentOrderRequest(BaseModel):
    amount_yuan: int = Field(ge=1, le=1000)


class PaymentOrderResponse(BaseModel):
    order_id: str
    amount_yuan: int
    credits: int
    status: str
    order_string: str | None = None
