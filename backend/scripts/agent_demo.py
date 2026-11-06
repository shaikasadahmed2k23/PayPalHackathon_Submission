"""Real-sandbox demo of the online shopping agent (Mode A) through the running API.

1. Needs an LLM key in .env (GEMINI_API_KEY or GROQ_API_KEY) and the server running:
       python -m uvicorn app.main:app --port 8000
2. Run (from backend/):  python scripts/agent_demo.py
"""

import asyncio
import base64
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.paypal import PayPalClient, PayPalError  # noqa: E402

API = os.environ.get("BACKEND_URL", "http://localhost:8000")
RULES = "Food and transport only, max $15 per purchase, never alcohol, ask me above $8"
MESSAGES = [
    "Get me snacks for a movie night, keep it under $8",
    "Also grab a 6-pack of beer",
    "Book me a ride home",
    "Return the popcorn",
    "How much have I spent?",
]


def call(api: httpx.Client, method: str, path: str, **kw: Any) -> Any:
    r = api.request(method, path, **kw)
    if r.is_error:
        sys.exit(f"{method} {path} -> {r.status_code}\n{r.text}")
    return r.json()


def main() -> None:
    api = httpx.Client(base_url=API, timeout=120)
    parsed = call(api, "POST", "/policy/parse", json={"text": RULES})
    print("\nPolicy summary:")
    for line in parsed["summary"]:
        print("  -", line)
    for note in parsed["ambiguities"]:
        print("  ? ", note)
    pid = call(api, "POST", "/policy", json={"raw_text": RULES, "policy": parsed["policy"]})["id"]

    created = call(api, "POST", "/allowance", json={"policy_id": pid, "amount": 30})
    aid = created["allowance_id"]
    api.headers["X-Owner-Token"] = created["owner_token"]  # owner-only routes: authorize, close, approvals...
    print("\nOpen this, log in with the SANDBOX PERSONAL account, approve:\n", created["approve_url"])
    input("\nPress Enter after approving...")

    device = SigningKey.generate()
    pub = base64.b64encode(bytes(device.verify_key)).decode()
    call(api, "POST", f"/allowance/{aid}/authorize", json={"device_pubkey_b64": pub})

    cid = None
    for i, message in enumerate(MESSAGES, 1):
        out = call(api, "POST", "/agent/chat",
                   json={"allowance_id": aid, "message": message, "conversation_id": cid})
        cid = out["conversation_id"]
        print(f"\n[{i}] You:   {message}\n    Agent: {out['reply']}   (intent: {out['intent']})")
        for p in out["purchases"]:
            print(f"      purchase #{p['id']} {p['merchant']} ${p['amount']:.2f} {p['status']}"
                  f" capture={p['capture_id']} approval={p['approval_id']}")
        for s in out["skipped"]:
            print(f"      skipped: {s['item']} - {'; '.join(s['reasons'])}")
        for r in out["refunds"]:
            print(f"      refund {r['refund_id']} ${r['amount']:.2f} {r['status']} ({', '.join(r['items'])})")

    # Anything that needed approval is shown here; approve it in the UI later (Task 6).
    pending = call(api, "GET", "/approvals", params={"status": "PENDING"})
    if pending:
        print("\nPending approvals:", [(a["id"], a["merchant_name"], a["amount"]) for a in pending])

    info = call(api, "GET", f"/allowance/{aid}")
    print("\nAllowance:", {k: info[k] for k in ("status", "amount", "captured_total", "remaining", "spent_today")})
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
