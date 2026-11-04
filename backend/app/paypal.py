"""All PayPal REST calls live here and nowhere else."""

import asyncio
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import httpx

from app.config import get_settings

Money = Decimal | float | int | str


class PayPalError(Exception):
    """A failed PayPal call: HTTP status plus PayPal's own error details."""

    def __init__(
        self,
        status: int,
        message: str,
        name: Optional[str] = None,
        details: Optional[list[dict[str, Any]]] = None,
        debug_id: Optional[str] = None,
    ) -> None:
        self.status = status
        self.message = message
        self.name = name
        self.details = details or []
        self.debug_id = debug_id
        super().__init__(str(self))

    def __str__(self) -> str:
        parts = [f"PayPal {self.status}"]
        if self.name:
            parts.append(self.name)
        parts.append(self.message)
        text = ": ".join(parts)
        issues = [d.get("issue") or d.get("description") for d in self.details]
        issues = [i for i in issues if i]
        if issues:
            text += f" [{'; '.join(issues)}]"
        if self.debug_id:
            text += f" (debug_id={self.debug_id})"
        return text


def fmt_amount(amount: Money) -> str:
    """PayPal wants a 2-decimal string, e.g. 3 -> '3.00'."""
    return str(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class PayPalClient:
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        currency: str = "USD",
    ) -> None:
        s = get_settings()
        self.client_id = client_id or s.paypal_client_id
        self.client_secret = client_secret or s.paypal_client_secret
        self.base_url = (base_url or s.paypal_base_url).rstrip("/")
        self.currency = currency
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "PayPalClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---- internals -------------------------------------------------------

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expiry:
                return self._token
            if not (self.client_id and self.client_secret):
                raise PayPalError(0, "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
            resp = await self._http.post(
                "/v1/oauth2/token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
            )
            if resp.is_error:
                raise self._error(resp)
            data = resp.json()
            self._token = data["access_token"]
            # Refresh 60s early so a token never expires mid-request.
            self._token_expiry = time.monotonic() + int(data["expires_in"]) - 60
            return self._token

    @staticmethod
    def _error(resp: httpx.Response) -> PayPalError:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return PayPalError(
            status=resp.status_code,
            message=body.get("message") or body.get("error_description") or resp.text[:300],
            name=body.get("name") or body.get("error"),
            details=body.get("details"),
            debug_id=body.get("debug_id"),
        )

    async def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {await self._get_token()}",
            "Content-Type": "application/json",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # idempotency key
        try:
            resp = await self._http.request(method, path, headers=headers, json=body)
        except httpx.HTTPError as e:
            raise PayPalError(0, f"network error: {e!r}") from e
        if resp.is_error:
            raise self._error(resp)
        return resp.json() if resp.content else {}

    def _money(self, amount: Money) -> dict[str, str]:
        return {"currency_code": self.currency, "value": fmt_amount(amount)}

    # ---- orders / authorizations ----------------------------------------

    async def create_allowance_order(
        self, amount: Money, return_url: str, cancel_url: str
    ) -> tuple[str, str]:
        """Create an AUTHORIZE order (a hold). Returns (order_id, approve_url)."""
        order = await self._request(
            "POST",
            "/v2/checkout/orders",
            {
                "intent": "AUTHORIZE",
                "purchase_units": [
                    {"amount": self._money(amount), "description": "LeashPay allowance"}
                ],
                "payment_source": {
                    "paypal": {
                        "experience_context": {
                            "return_url": return_url,
                            "cancel_url": cancel_url,
                            "user_action": "CONTINUE",
                        }
                    }
                },
            },
        )
        approve_url = next(
            (l["href"] for l in order.get("links", []) if l["rel"] in ("payer-action", "approve")),
            None,
        )
        if not approve_url:
            raise PayPalError(200, "order response had no approve link")
        return order["id"], approve_url

    async def authorize_order(self, order_id: str) -> str:
        """After buyer approval: place the hold. Returns authorization_id."""
        data = await self._request("POST", f"/v2/checkout/orders/{order_id}/authorize", {})
        try:
            return data["purchase_units"][0]["payments"]["authorizations"][0]["id"]
        except (KeyError, IndexError) as e:
            raise PayPalError(200, "authorize response had no authorization id") from e

    async def capture(
        self, authorization_id: str, amount: Money, final: bool, request_id: str
    ) -> tuple[str, str]:
        """Partial capture against the hold. Returns (capture_id, status)."""
        data = await self._request(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/capture",
            {"amount": self._money(amount), "final_capture": final},
            request_id=request_id,
        )
        return data["id"], data["status"]

    async def void(self, authorization_id: str) -> None:
        await self._request("POST", f"/v2/payments/authorizations/{authorization_id}/void")

    async def get_authorization(self, authorization_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/payments/authorizations/{authorization_id}")

    # ---- refunds / payouts ----------------------------------------------

    async def refund(
        self, capture_id: str, amount: Money, request_id: str
    ) -> tuple[str, str]:
        """Refund (part of) a capture. Returns (refund_id, status)."""
        data = await self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            {"amount": self._money(amount)},
            request_id=request_id,
        )
        return data["id"], data["status"]

    async def payout(
        self, receiver_email: str, amount: Money, sender_batch_id: str, note: str
    ) -> tuple[str, str]:
        """Pay a merchant by email. Returns (payout_batch_id, batch_status).

        PayPal rejects a reused sender_batch_id, so it doubles as idempotency.
        """
        data = await self._request(
            "POST",
            "/v1/payments/payouts",
            {
                "sender_batch_header": {
                    "sender_batch_id": sender_batch_id,
                    "email_subject": "You received a LeashPay payment",
                    "email_message": note,
                },
                "items": [
                    {
                        "recipient_type": "EMAIL",
                        "receiver": receiver_email,
                        "amount": self._money(amount),
                        "note": note,
                        "sender_item_id": sender_batch_id,
                    }
                ],
            },
            request_id=sender_batch_id,
        )
        header = data["batch_header"]
        return header["payout_batch_id"], header["batch_status"]
