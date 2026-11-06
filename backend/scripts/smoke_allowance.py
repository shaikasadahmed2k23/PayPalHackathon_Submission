"""Real-sandbox smoke test of the allowance lifecycle, through the running API.

1. Start the server:   python -m uvicorn app.main:app --port 8000
2. Run (from backend/): python scripts/smoke_allowance.py
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.paypal import PayPalClient, PayPalError  # noqa: E402

API = os.environ.get("BACKEND_URL", "http://localhost:8000")


def show(title: str, data: object) -> None:
    print(f"\n== {title}\n{json.dumps(data, indent=2, default=str)}")


def call(api: httpx.Client, method: str, path: str, **kw) -> dict:
    r = api.request(method, path, **kw)
    if r.is_error:
        sys.exit(f"{method} {path} -> {r.status_code}\n{r.text}")
    return r.json()


def verdict_line(label: str, r: dict) -> None:
    v = r["verdict"]
    print(f"\n{label}: {v['decision']}  capture_id={r['capture_id']}  approval_id={r['approval_id']}")
    for reason in v["reasons"]:
        print("   -", reason)


def main() -> None:
    api = httpx.Client(base_url=API, timeout=60)
    policy = {
        "allowed_categories": ["food"],
        "per_tx_max": 10,
        "require_approval_above": 6,
        "offline": {"enabled": True, "per_tap_max": 5},
    }
    pid = call(api, "POST", "/policy", json={"raw_text": "smoke: food only", "policy": policy})["id"]

    created = call(api, "POST", "/allowance", json={"policy_id": pid, "amount": 20})
    aid = created["allowance_id"]
    api.headers["X-Owner-Token"] = created["owner_token"]  # owner-only routes: authorize, close, approvals...
    print("\nOpen this, log in with the SANDBOX PERSONAL account, approve:\n", created["approve_url"])
    input("\nPress Enter after approving...")

    device = SigningKey.generate()
    pub = base64.b64encode(bytes(device.verify_key)).decode()
    auth = call(api, "POST", f"/allowance/{aid}/authorize", json={"device_pubkey_b64": pub})
    show("certificate", auth["certificate"])

    def spend(amount: float, category: str) -> dict:
        return call(api, "POST", f"/allowance/{aid}/test-spend",
                    json={"amount": amount, "category": category, "merchant_name": "Demo Cafe"})

    verdict_line("$3 food", spend(3, "food"))
    r8 = spend(8, "food")
    verdict_line("$8 food", r8)
    approved = call(api, "POST", f"/approvals/{r8['approval_id']}/approve")
    verdict_line(f"approve #{r8['approval_id']}", approved)
    verdict_line("$4 alcohol", spend(4, "alcohol"))

    info = call(api, "GET", f"/allowance/{aid}")
    show("allowance before close", {k: info[k] for k in
         ("status", "amount", "captured_total", "remaining", "spent_today")})

    closed = call(api, "POST", f"/allowance/{aid}/close", params={"force": "true"})
    show("closed", closed)

    async def final_status() -> str:
        async with PayPalClient() as pp:
            return (await pp.get_authorization(info["authorization_id"]))["status"]

    print("\nFinal PayPal authorization status:", asyncio.run(final_status()))


if __name__ == "__main__":
    try:
        main()
    except PayPalError as e:
        sys.exit(f"FAILED: {e}")
