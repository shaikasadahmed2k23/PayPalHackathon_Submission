# LeashPay MCP: bring your own agent

Any AI agent that speaks [MCP](https://modelcontextprotocol.io) (Claude Desktop, MCP Inspector, ...) can shop
the LeashPay demo store with **your** allowance. The agent is the brain; LeashPay is the leash.

| The agent **can** | The agent **can never** |
|---|---|
| read the budget and the rules it must obey | change rules or raise limits |
| search the demo store | approve its own `NEEDS_APPROVAL` purchases |
| buy (the Policy Judge decides every cent) | close the allowance |
| list / check purchases, ask for refunds | use anyone else's allowance |

Prices always come from LeashPay's database, never from the agent. Every call is written to the audit log
as actor `external_agent` with the agent's name and key prefix.

```
Claude Desktop ──stdio──> mcp_server/server.py ──HTTPS + X-Agent-Key──> /agent-api/*  ──> Policy Judge ──> PayPal
Remote MCP client ───────────── Streamable HTTP + Bearer key ────────> /mcp          ──┘
```

## 1. Create an agent key

1. Open the LeashPay wallet, scroll to **AI agents on your leash**, tap **Connect an agent**.
2. Name it (e.g. `Claude Desktop`) and optionally set a **per-purchase cap**. A purchase above the cap is
   refused with `Over the $X cap you set for <name>`.
3. Copy the key (`lpk_...`) **now**: it is shown once and only a hash is stored. **Revoke** any time.

A key belongs to one allowance and stops working when that allowance is closed or expired.

## 2. Claude Desktop on Windows (stdio)

Install once (use any Python 3.10+; the path to *that* python is what Claude Desktop needs):

```powershell
cd D:\path\to\LeashPay
python -m venv mcp_server\venv
mcp_server\venv\Scripts\python -m pip install -r mcp_server\requirements.txt
```

Open `%APPDATA%\Claude\claude_desktop_config.json` (Claude Desktop → Settings → Developer → Edit Config)
and add:

```json
{
  "mcpServers": {
    "leashpay": {
      "command": "D:\\path\\to\\LeashPay\\mcp_server\\venv\\Scripts\\python.exe",
      "args": ["D:\\path\\to\\LeashPay\\mcp_server\\server.py"],
      "env": {
        "LEASHPAY_API_URL": "https://leashpay-api.onrender.com",
        "LEASHPAY_AGENT_KEY": "lpk_your_key_here"
      }
    }
  }
}
```

Use **full paths** and double backslashes. Fully quit Claude Desktop (tray icon → Quit) and reopen it. The
tools icon in the chat box should list `leashpay`. Then try:

- "Check my LeashPay budget and rules."
- "Buy snacks for movie night, keep it under $8."
- "Buy me a beer." (the Policy Judge blocks it and Claude relays the exact reason)
- "Buy 3 popcorns." (if that is above your approval limit, approve it in the wallet, then ask Claude to check it)
- "Refund the popcorn."

The first call after idle can take ~30 s while the free Render instance wakes up.

## 3. Test with MCP Inspector

```powershell
$env:LEASHPAY_API_URL = "https://leashpay-api.onrender.com"
$env:LEASHPAY_AGENT_KEY = "lpk_your_key_here"
npx @modelcontextprotocol/inspector D:\path\to\LeashPay\mcp_server\venv\Scripts\python.exe D:\path\to\LeashPay\mcp_server\server.py
```

Open the printed URL, click **Connect**, **Tools → List Tools**, and run `leashpay_wallet`.

## 4. Remote endpoint (no local install)

The backend also serves MCP over Streamable HTTP at `https://leashpay-api.onrender.com/mcp`
(stateless, JSON responses). Send the key on **every** request: `Authorization: Bearer lpk_...`
(or `X-Agent-Key`). Same six tools, same Policy Judge.

```powershell
# MCP Inspector: choose transport "Streamable HTTP", URL above, and add header Authorization: Bearer lpk_...
```

Clients that cannot send custom headers cannot use this endpoint; use the stdio server instead.

## Tools

| Tool | What it does |
|---|---|
| `leashpay_wallet` | remaining budget, spent today, the rules, your cap |
| `leashpay_search_store(query)` | demo-store products with `product_id`, price, merchant |
| `leashpay_buy(items, note)` | `items = [{"product_id": 3, "qty": 1}]`; returns PASS / NEEDS_APPROVAL / FAIL with reasons |
| `leashpay_purchases()` | purchases on this allowance |
| `leashpay_check_purchase(purchase_id)` | poll a `NEEDS_APPROVAL` purchase |
| `leashpay_refund(purchase_id, product_id?)` | refund a whole purchase or one item |

## REST API (what the stdio server calls)

Header `X-Agent-Key: lpk_...`. `GET /agent-api/wallet`, `GET /agent-api/products?q=`,
`POST /agent-api/buy {items, note?}`, `GET /agent-api/purchases`, `GET /agent-api/purchases/{id}`,
`POST /agent-api/refund {purchase_id, product_id?}`. Errors: `401` bad/revoked key, `403` allowance closed
or a route outside the agent API.

## Security notes (read this)

- A request carrying an agent key is refused (`403`) on every route except `/agent-api`, `/mcp` and `/health`,
  so a well-behaved agent cannot reach `/approvals`, `/policy` or `/allowance/.../close`.
- **Owner token (no login, but owner actions are protected).** Creating an allowance returns an `lpo_` token
  once (only its sha256 is stored); the wallet keeps it in IndexedDB and sends `X-Owner-Token` for authorize,
  approve / deny, close, agent-key create / list / revoke, `issued` and `test-spend`. Without it those routes
  answer `401`. An agent key never passes: it is a different credential and is refused on those routes anyway.
  Reads used by the dashboard, `/policy/parse`, `/vouchers/settle` (signature-protected), `/merchants`,
  `/products` and `/health` stay open. Allowances created before this existed have no token and stay usable.
- **Honest limits:** there is still no user login. Whoever holds the owner token is the owner, so it lives only
  on the device that created the allowance (lose it and you cannot approve or close from another device).
  `/agent/chat` and the read-only dashboard endpoints are open in this single-demo-user app.
- **Balance wording:** spent money cannot go back onto a PayPal hold. A refund lands in the buyer's PayPal
  balance, so `Left on hold` does not grow and `Spent` (gross captured) does not shrink; refunds are reported
  separately (`refunded_total`, `refunded_today`).
- The key is sent in a header over HTTPS; treat it like a password. Revoke it from the wallet if it leaks.
- Sandbox PayPal only. Amounts are fake money.
