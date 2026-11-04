# LeashPay — Project Spec for Claude Code

> PayPal AI Hackathon (Devpost). Deadline: **Nov 12 2026, 3:00pm EST**. Solo builder: Asad.
> Read this file fully before every task. Build ONLY what the current task asks. Do not add features not listed here.

## One-line pitch
**A PayPal wallet with a leash: you set spending rules in plain English, an AI agent enforces them — online and even with zero internet.**

## The core idea (understand this first)
1. User writes rules in English → LLM converts to a **Policy JSON**.
2. User creates an **Allowance** (e.g. $50). PayPal Orders v2 with `intent: AUTHORIZE` → money is **HELD**, not taken. User approves once on PayPal.
3. Every spend (online agent purchase OR offline voucher) is a **partial capture** against that one authorization — but only after the **Policy Judge** says PASS.
4. When the allowance is closed, the last capture uses `final_capture: true` (or the authorization is voided) → the unused money returns to the buyer.
5. Every decision is written to an **audit log**.

Proven working in sandbox (`test_hold.py`): authorize $20 → capture $3 → capture $2 final → status CAPTURED.

## Two modes, one engine

### Mode A — Online: AI Shopping Agent
- Chat: *"Get snacks for movie night under $15"* → agent searches the **demo store catalog** (our own seeded products, NOT the real internet) → proposes a cart → Policy Judge → PASS: capture | NEEDS_APPROVAL: create pending approval | FAIL: block + plain-English reason.
- Agent can refund: *"Return the chips"* → refund the capture.

### Mode B — Offline: Show-to-Pay vouchers (festival/mela with no network)
- Buyer phone (offline) shows a rotating **signed voucher QR**. One step for buyer.
- Merchant phone (offline) scans QR, types amount, verifies everything locally, stores voucher in a local queue.
- When merchant is back online → syncs vouchers → **Settlement Agent** verifies, dedupes, judges, captures from the authorization, and pays the merchant via **PayPal Payouts**.

## Offline security model (must implement exactly)
- On allowance creation, the buyer device generates an **Ed25519 keypair** (`@noble/ed25519`). Private key stays on device (IndexedDB). Public key sent to backend.
- Backend issues an **Allowance Certificate** signed with the server Ed25519 key (`PyNaCl`):
  `{allowance_id, device_pubkey, per_tap_max, max_vouchers, blocked_categories, expires_at}`
  - `max_vouchers = floor(allowance_amount / per_tap_max)` → total offline exposure can never exceed the hold.
- **Voucher** = `{allowance_id, serial (int, increments), issued_at}` signed by the device key. Voucher QR = cert + voucher + signature. QR rotates every 30s or after each use.
- Merchant app verifies **offline**: server signature on cert (server pubkey cached in app), device signature on voucher, `serial < max_vouchers`, `amount ≤ per_tap_max`, merchant category not in `blocked_categories`, serial not already in local queue, not expired.
- Server at settlement: re-verify all signatures, `UNIQUE(allowance_id, serial)` in DB, policy checks, then capture with header `PayPal-Request-Id: voucher-{allowance_id}-{serial}` (PayPal-level idempotency = no double charge).
- Known limitation (document in README, don't over-engineer): a screenshot of the QR could be presented to two merchants; the server accepts the first and rejects the second; exposure is bounded by `per_tap_max`.

## Policy Judge — design rule (IMPORTANT)
- **Deterministic rule engine decides. LLM never approves money on its own.**
- LLM is used for: (1) parsing English rules → Policy JSON, (2) classifying item/merchant category when unknown, (3) anomaly review of voucher batches (e.g. 6 vouchers in 2 min at one merchant), (4) writing the plain-English explanation.
- Verdicts: `PASS` | `NEEDS_APPROVAL` | `FAIL`, always with `reasons: list[str]`.

### Policy JSON schema
```json
{
  "currency": "USD",
  "per_tx_max": 20.0,
  "daily_max": 60.0,
  "require_approval_above": 10.0,
  "allowed_categories": ["food", "groceries", "transport"],
  "blocked_categories": ["alcohol", "electronics", "gambling"],
  "blocked_merchants": [],
  "offline": { "enabled": true, "per_tap_max": 5.0 }
}
```
Empty `allowed_categories` = all allowed except blocked.

## Tech stack (do not change without asking)
- **Backend:** Python, FastAPI, SQLModel, `httpx` for PayPal REST, PyNaCl, pydantic-settings. SQLite locally (`DATABASE_URL`), Postgres in prod.
- **LLM:** provider via env `LLM_PROVIDER=gemini|groq`. Structured JSON output. Wrap in one module `app/llm.py`.
- **Frontend:** Next.js (App Router, TypeScript) as a **PWA** that works offline (Serwist service worker). Tailwind. QR show: `qrcode.react`. QR scan: `@zxing/browser` or `html5-qrcode`. Offline storage: IndexedDB (`idb`).
- **Dashboard:** AG Grid (audit log, vouchers, decisions) — sponsor prize.
- **Deploy:** Render (backend web service + frontend) — sponsor prize.
- **PayPal:** Sandbox only. Currency **USD** (sandbox seller + buyer are US accounts). Base URL `https://api-m.sandbox.paypal.com`.

## PayPal endpoints used
| Purpose | Endpoint |
|---|---|
| Access token | `POST /v1/oauth2/token` (basic auth, cache until expiry) |
| Create allowance order | `POST /v2/checkout/orders` (`intent: AUTHORIZE`) |
| Authorize after approval | `POST /v2/checkout/orders/{id}/authorize` |
| Spend | `POST /v2/payments/authorizations/{auth_id}/capture` (`final_capture` false/true) |
| Close allowance | `POST /v2/payments/authorizations/{auth_id}/void` |
| Check status | `GET /v2/payments/authorizations/{auth_id}` |
| Refund | `POST /v2/payments/captures/{capture_id}/refund` |
| Pay merchant | `POST /v1/payments/payouts` (`sender_batch_id` = voucher id) |
Always send `PayPal-Request-Id` on POSTs that move money.

## Data models (SQLModel)
- `Policy`: id, raw_text, policy_json, created_at
- `Allowance`: id, policy_id, amount, captured_total, paypal_order_id, authorization_id, status (`CREATED|APPROVED|ACTIVE|CLOSED|EXPIRED`), device_pubkey, certificate (json), expires_at
- `Merchant`: id, name, category, paypal_email
- `Product` (demo store): id, name, price, category, merchant_id
- `Voucher`: id, allowance_id, serial, merchant_id, amount, signature, issued_at, scanned_at, status (`PENDING|SETTLED|REJECTED`), capture_id, payout_batch_id, judge_reasons
- `Purchase` (online agent): id, allowance_id, items_json, amount, status (`CAPTURED|PENDING_APPROVAL|BLOCKED|REFUNDED`), capture_id, judge_reasons
- `AuditLog`: id, ts, actor (`user|shopping_agent|policy_judge|settlement_agent|merchant`), action, verdict, reasons, paypal_ref, payload_json

## Folder structure
```
leashpay/
  CLAUDE.md
  README.md
  LICENSE              (MIT)
  backend/
    app/
      main.py  config.py  db.py  models.py
      paypal.py          # all PayPal REST calls live here only
      llm.py             # all LLM calls live here only
      crypto.py          # server keys, cert signing, voucher verification
      policy/ parser.py  judge.py
      agents/ shopping_agent.py  settlement_agent.py
      routers/ policy.py allowance.py vouchers.py agent.py approvals.py audit.py merchants.py
      seed.py            # demo merchants + products
    tests/
    requirements.txt  .env.example
  frontend/
    app/ setup/ wallet/ merchant/ agent/ dashboard/
```

## Frontend screens
1. `/setup` — textarea for English rules → show parsed policy card → amount → "Create Allowance" → redirect to PayPal → `/setup/return` authorizes → generate device key + store cert.
2. `/wallet` (buyer) — allowance left, big **Show-to-Pay QR** (works offline), pending approvals (Approve/Deny), activity list.
3. `/merchant` — pick demo merchant, scan QR, enter amount, local verification result (green/red with reason), offline queue count, **Sync** button.
4. `/agent` — chat with shopping agent, shows cart + judge verdict cards.
5. `/dashboard` — AG Grid tables: Audit log, Vouchers, Purchases.
Show a clear **"OFFLINE"** badge when `navigator.onLine` is false.

## Out of scope (do NOT build)
Real-world shopping/scraping, NFC/Bluetooth, real INR/UPI, user auth/multi-user login (single demo user is fine), mobile native apps, email/SMS.

## Working rules for Claude Code
- One task at a time. After each task: run it, show how to test it, stop.
- Keep PayPal logic only in `paypal.py`, LLM logic only in `llm.py`.
- Never hardcode secrets. Read from `.env`. Keep `.env.example` updated.
- Write small tests for judge, crypto, and settlement dedupe.
- Prefer simple, readable code over clever code. Type hints everywhere.
