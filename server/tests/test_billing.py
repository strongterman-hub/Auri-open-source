from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.billing.alipay import AlipayClient
from app.billing.service import BillingService
from app.billing.store import CREDIT_MICROS, BillingStore


@pytest.fixture
def client(tmp_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AURI_DATA_DIR", str(tmp_dir))
    monkeypatch.setenv("AURI_LLM_PROVIDER", "echo")
    monkeypatch.setenv("AURI_PROACTIVE_QUIET_HOURS", "")

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _private_key_text(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def _public_key_text(key) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _alipay_client() -> tuple[AlipayClient, object]:
    app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alipay_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (
        AlipayClient(
            app_id="2021000000000000",
            private_key=_private_key_text(app_key),
            alipay_public_key=_public_key_text(alipay_key),
            gateway="https://example.invalid/gateway.do",
            notify_url="https://example.invalid/v1/billing/alipay/notify",
        ),
        alipay_key,
    )


def _sign_notification(client: AlipayClient, private_key, params: dict[str, str]) -> str:
    signature = private_key.sign(
        client.canonical(params).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def test_request_signature_includes_sign_type() -> None:
    client, _alipay_private_key = _alipay_client()
    params = {"app_id": "2021000000000000", "sign_type": "RSA2"}

    signature = base64.b64decode(client.sign(params))
    client._private_key.public_key().verify(
        signature,
        client.canonical(params, exclude_sign_type=False).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_welcome_credit_is_exactly_once(tmp_dir: Path) -> None:
    store = BillingStore(tmp_dir / "billing.db", welcome_credits=500)

    assert store.ensure_welcome_credit("u1") is True
    assert store.ensure_welcome_credit("u1") is False
    assert store.balance_micros("u1") == 500 * CREDIT_MICROS


def test_usage_is_charged_by_actual_yuan_cost(tmp_dir: Path) -> None:
    store = BillingStore(tmp_dir / "billing.db", welcome_credits=500)
    service = BillingService(store, alipay=None, enforcement_enabled=True)

    service.charge_usage(
        {
            "id": "usage-1",
            "user_id": "u1",
            "model": "deepseek-v4-flash",
            "kind": "chat",
            "ts": "2026-09-04T00:00:00+00:00",
            "cached_tokens": 0,
            "uncached_tokens": 1_000_000,
            "completion_tokens": 0,
        }
    )
    service.charge_usage(
        {
            "id": "usage-1",
            "user_id": "u1",
            "model": "deepseek-v4-flash",
            "kind": "chat",
            "ts": "2026-09-04T00:00:00+00:00",
            "cached_tokens": 0,
            "uncached_tokens": 1_000_000,
            "completion_tokens": 0,
        }
    )

    assert service.balance_text("u1") == "350.00"


def test_manual_credit_grant_is_positive_and_idempotent(tmp_dir: Path) -> None:
    store = BillingStore(tmp_dir / "billing.db", welcome_credits=500)

    assert store.grant_credits("u1", 100, reference="manual:test-grant") is True
    assert store.grant_credits("u1", 100, reference="manual:test-grant") is False
    assert store.balance_micros("u1") == 600 * CREDIT_MICROS

    with pytest.raises(ValueError, match="positive"):
        store.grant_credits("u1", 0, reference="manual:invalid")


def test_paid_notification_credits_order_only_once(tmp_dir: Path) -> None:
    store = BillingStore(tmp_dir / "billing.db", welcome_credits=500)
    alipay, alipay_private_key = _alipay_client()
    service = BillingService(store, alipay=alipay, credits_per_yuan=95)
    order = store.create_order("u1", 10, 95)
    params = {
        "app_id": alipay.app_id,
        "out_trade_no": order.order_id,
        "trade_no": "2026090400001",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "10.00",
        "sign_type": "RSA2",
    }
    params["sign"] = _sign_notification(alipay, alipay_private_key, params)

    assert service.handle_alipay_notification(params) is True
    assert service.handle_alipay_notification(params) is True
    assert service.balance_text("u1") == "1450.00"
    assert store.get_order(order.order_id, "u1").status == "paid"


def test_paid_notification_rejects_amount_mismatch(tmp_dir: Path) -> None:
    store = BillingStore(tmp_dir / "billing.db", welcome_credits=500)
    alipay, alipay_private_key = _alipay_client()
    service = BillingService(store, alipay=alipay, credits_per_yuan=95)
    order = store.create_order("u1", 10, 95)
    params = {
        "app_id": alipay.app_id,
        "out_trade_no": order.order_id,
        "trade_no": "2026090400002",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "1.00",
        "sign_type": "RSA2",
    }
    params["sign"] = _sign_notification(alipay, alipay_private_key, params)

    assert service.handle_alipay_notification(params) is False
    assert service.balance_text("u1") == "500.00"


def test_registered_user_receives_welcome_balance(client) -> None:
    registered = client.post(
        "/v1/auth/register",
        json={"email": "credits@example.com", "password": "test1234"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}

    response = client.get("/v1/billing/balance", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "balance_credits": "500.00",
        "welcome_credits": 500,
        "credits_per_yuan": 95,
    }


def test_order_creation_is_disabled_without_server_keys(client) -> None:
    registered = client.post(
        "/v1/auth/register",
        json={"email": "no-keys@example.com", "password": "test1234"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}

    response = client.post(
        "/v1/billing/orders", json={"amount_yuan": 100}, headers=headers
    )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "支付宝支付尚未配置"
