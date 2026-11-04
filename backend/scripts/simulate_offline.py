"""Real-sandbox simulation of the offline flow, acting as the buyer phone and merchant phones.

1. Set MERCHANT_PAYOUT_EMAIL in backend/.env (a sandbox business account), start the server:
       python -m uvicorn app.main:app --port 8000
2. Run (from backend/):  python scripts/simulate_offline.py
"""

import asyncio
import base64
import itertools
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.crypto import canonical_json  # noqa: E402
from app.paypal import PayPalClient, PayPalError  # noqa: E402

API = os.environ.get("BACKEND_URL", "http://localhost:8000")


def now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def call(api: httpx.Client, method: str, path: str, **kw: Any) -> Any:
    r = api.request(method, path, **kw)
    if r.is_error:
        sys.exit(f"{method} {path} -> {r.status_code}\n{r.text}")
    return r.json()


def main() -> None:
    api = httpx.Client(base_url=API, timeout=90)
    merchants = {m["name"]: m["id"] for m in call(api, "GET", "/merchants")}

    policy = {"allowed_categories": ["food"], "per_tx_max": 20, "require_approval_above": 10,
              "offline": {"enabled": True, "per_tap_max": 5}}
    pid = call(api, "POST", "/policy", json={"raw_text": "simulate: food only offline", "policy": policy})["id"]
    created = call(api, "POST", "/allowance", json={"policy_id": pid, "amount": 20})
    aid = created["allowance_id"]
    print("\nOpen this, log in with the SANDBOX PERSONAL account, approve:\n", created["approve_url"])
    input("\nPress Enter after approving...")

    device = SigningKey.generate()  # the buyer phone keeps this key
    pub = base64.b64encode(bytes(device.verify_key)).decode()
    auth = call(api, "POST", f"/allowance/{aid}/authorize", json={"device_pubkey_b64": pub})
    cert, cert_sig = auth["certificate"], auth["signature"]
    print("\nCertificate:", json.dumps(cert))

    def phone_payment(serial: int, amount: float, merchant: str) -> dict:
        """What the buyer phone signs and the merchant phone stores in its offline queue."""
        voucher = {"allowance_id": aid, "serial": serial, "issued_at": now_z()}
        sig = base64.b64encode(device.sign(canonical_json(voucher)).signature).decode()
        return {"merchant": merchant, "payment": {
            "certificate": cert, "cert_signature": cert_sig, "voucher": voucher,
            "device_signature": sig, "amount": amount,
            "merchant_id": merchants[merchant], "scanned_at": now_z()}}

    queue = [
        phone_payment(0, 3.00, "Chai Corner"),         # valid
        phone_payment(1, 4.50, "Taco Truck"),          # valid
        phone_payment(1, 4.50, "Chai Corner"),         # replay of serial 1
        phone_payment(2, 7.00, "Chai Corner"),         # over per-tap cap
        phone_payment(3, 2.00, "Craft Beer Tent"),     # not an allowed category
        phone_payment(9, 1.00, "Chai Corner"),         # serial >= max_vouchers
    ]

    # Each merchant phone syncs the consecutive run of vouchers it holds.
    stuck = []
    for merchant, group in itertools.groupby(queue, key=lambda q: q["merchant"]):
        payments = [g["payment"] for g in group]
        report = call(api, "POST", "/vouchers/settle",
                      json={"merchant_id": merchants[merchant], "payments": payments})
        print(f"\n=== {merchant}: {report['summary']}")
        for it in report["items"]:
            print(f"  serial {it['serial']}  ${it['amount']:.2f}  {it['status']}"
                  f"  capture={it['capture_id']}  payout={it['payout_batch_id']}")
            for reason in it["reasons"]:
                print("      -", reason)
            if it["status"] == "SETTLED_PAYOUT_PENDING":
                stuck.append(it)
        print("  AI review:", report["ai_review"])

    if stuck:
        print("\nPAYOUT FAILED. PayPal's exact error:")
        for it in stuck:
            print("  ", it["reasons"][0])
        sys.exit("Stopping here without closing the allowance (captures are kept, payouts pending).")

    info = call(api, "GET", f"/allowance/{aid}")
    print("\nAllowance:", {k: info[k] for k in ("status", "amount", "captured_total", "remaining")})
    print("Vouchers in DB:", [(v["serial"], v["status"]) for v in call(api, "GET", f"/vouchers?allowance_id={aid}")])
    print("Closed:", call(api, "POST", f"/allowance/{aid}/close", params={"force": "true"})["status"])

    async def final_status() -> str:
        async with PayPalClient() as pp:
            return (await pp.get_authorization(info["authorization_id"]))["status"]

    print("Final PayPal authorization status:", asyncio.run(final_status()))


if __name__ == "__main__":
    try:
        main()
    except PayPalError as e:
        sys.exit(f"FAILED: {e}")
