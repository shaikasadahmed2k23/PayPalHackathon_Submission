import os, uuid, requests
from dotenv import load_dotenv

load_dotenv()
BASE = "https://api-m.sandbox.paypal.com"
CID, SECRET = os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]

def get_token():
    r = requests.post(f"{BASE}/v1/oauth2/token", auth=(CID, SECRET),
                      data={"grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]

TOKEN = get_token()

def call(method, path, body=None, request_id=None):
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    if request_id:
        headers["PayPal-Request-Id"] = request_id  # idempotency: same id = no double charge
    r = requests.request(method, f"{BASE}{path}", headers=headers, json=body)
    if not r.ok:
        print("ERROR", r.status_code, r.text)
        r.raise_for_status()
    return r.json()

# 1) Create allowance order — HOLD only, no capture
order = call("POST", "/v2/checkout/orders", {
    "intent": "AUTHORIZE",
    "purchase_units": [{
        "amount": {"currency_code": "USD", "value": "20.00"},
        "description": "LeashPay offline allowance"
    }],
    "payment_source": {"paypal": {"experience_context": {
        "return_url": "https://example.com/ok",
        "cancel_url": "https://example.com/cancel",
        "user_action": "CONTINUE"
    }}}
})
link = next(l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve"))
print("\nOpen this, login with SANDBOX PERSONAL account, approve:\n", link)
input("\nApprove karke Enter dabao...")

# 2) Authorize = money is now HELD
auth = call("POST", f"/v2/checkout/orders/{order['id']}/authorize", {})
auth_id = auth["purchase_units"][0]["payments"]["authorizations"][0]["id"]
print("✅ Held $20 | authorization:", auth_id)

# 3) Voucher #1 → capture $3 (keep authorization open)
c1 = call("POST", f"/v2/payments/authorizations/{auth_id}/capture",
          {"amount": {"currency_code": "USD", "value": "3.00"}, "final_capture": False},
          request_id="voucher-001")
print("☕ Captured $3:", c1["status"])

# 4) Voucher #2 → capture $2, FINAL → remaining $15 released to buyer
c2 = call("POST", f"/v2/payments/authorizations/{auth_id}/capture",
          {"amount": {"currency_code": "USD", "value": "2.00"}, "final_capture": True},
          request_id="voucher-002")
print("🍪 Captured $2 (final):", c2["status"])

print("📋 Authorization status:", call("GET", f"/v2/payments/authorizations/{auth_id}")["status"])