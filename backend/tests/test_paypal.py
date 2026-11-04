import asyncio
import json
import logging
import time

import httpx

from app.paypal import PayPalClient, PayPalError

DETAILS = [
    {"field": "items[0].amount.currency", "location": "body", "issue": "may not be null"},
    {"field": "items[0].receiver", "issue": "INVALID_EMAIL", "description": "Receiver is not a valid email"},
    {"description": "Something general"},
]


def test_error_message_lists_every_detail_with_field_issue_description():
    msg = str(PayPalError(400, "Invalid request - see details", name="VALIDATION_ERROR",
                          details=DETAILS, debug_id="abc"))
    assert "PayPal 400: VALIDATION_ERROR: Invalid request - see details" in msg
    assert "items[0].amount.currency: may not be null" in msg
    assert "items[0].receiver: INVALID_EMAIL - Receiver is not a valid email" in msg
    assert "Something general" in msg
    assert "debug_id=abc" in msg


def _client(handler) -> PayPalClient:
    pp = PayPalClient(client_id="id", client_secret="secret", base_url="https://paypal.test")
    pp._http = httpx.AsyncClient(base_url="https://paypal.test", transport=httpx.MockTransport(handler))
    pp._token, pp._token_expiry = "tok", time.monotonic() + 1000
    return pp


def test_payout_payload_matches_payouts_v1_api(caplog):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"batch_header": {"payout_batch_id": "B1", "batch_status": "PENDING"}})

    caplog.set_level(logging.INFO, logger="leashpay.paypal")
    result = asyncio.run(_client(handler).payout("m@test.com", 3, "voucher-A-0", "Chai"))
    assert result == ("B1", "PENDING")
    assert seen["path"] == "/v1/payments/payouts"
    assert seen["headers"]["paypal-request-id"] == "voucher-A-0"
    body = seen["body"]
    assert body["sender_batch_header"] == {
        "sender_batch_id": "voucher-A-0", "recipient_type": "EMAIL",
        "email_subject": "You received a LeashPay payment", "email_message": "Chai",
    }
    assert body["items"] == [{
        "recipient_type": "EMAIL", "amount": {"value": "3.00", "currency": "USD"},
        "receiver": "m@test.com", "note": "Chai", "sender_item_id": "voucher-A-0",
    }]
    assert "currency_code" not in json.dumps(body)  # v2-only field name
    assert "PayPal payout payload" in caplog.text and "3.00" in caplog.text


def test_paypal_error_response_keeps_all_details():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"name": "VALIDATION_ERROR", "message": "Invalid request - see details",
                                         "debug_id": "d1", "details": DETAILS})

    try:
        asyncio.run(_client(handler).payout("m@test.com", 3, "b", "n"))
    except PayPalError as e:
        assert e.status == 400 and "items[0].amount.currency: may not be null" in str(e)
    else:
        raise AssertionError("expected PayPalError")
