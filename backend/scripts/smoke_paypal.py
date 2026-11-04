"""Sandbox smoke test: authorize $20 -> capture $3 -> capture $2 (final).

Run from backend/:  python scripts/smoke_paypal.py
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.paypal import PayPalClient, PayPalError  # noqa: E402


async def main() -> None:
    run = uuid.uuid4().hex[:8]  # unique ids so re-runs don't collide on idempotency keys
    async with PayPalClient() as pp:
        order_id, approve_url = await pp.create_allowance_order(
            20, "https://example.com/ok", "https://example.com/cancel"
        )
        print("\nOpen this, log in with the SANDBOX PERSONAL account, approve:\n", approve_url)
        input("\nPress Enter after approving...")

        auth_id = await pp.authorize_order(order_id)
        print("Held $20 | authorization:", auth_id)

        cap1, status1 = await pp.capture(auth_id, 3, final=False, request_id=f"smoke-{run}-1")
        print("Captured $3:", cap1, status1)

        cap2, status2 = await pp.capture(auth_id, 2, final=True, request_id=f"smoke-{run}-2")
        print("Captured $2 (final):", cap2, status2)

        auth = await pp.get_authorization(auth_id)
        print("Authorization status:", auth["status"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except PayPalError as e:
        sys.exit(f"FAILED: {e}\ndetails: {e.details}")
