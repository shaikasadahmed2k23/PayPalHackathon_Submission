# LeashPay: 5-minute test path for judges

You need only a laptop browser (Chrome or Edge). No phone, no install.

- **App:** https://leashpay-krgp.onrender.com
- **PayPal sandbox buyer:** the credentials are in the **Devpost Testing Instructions**. Use only that account; it is a sandbox account and no real money moves.
- **Heads-up:** the API runs on a free Render plan and may take **30-60 seconds to wake** on the very first request. If the first screen says "Waking up the server", just wait.

## 1. Create an allowance (about 1 min)

1. Open **/setup**. Tap an example chip (e.g. **Festival day**), then **Understand my rules**. You get a policy card with limits, blocked categories and any ambiguities the AI noticed.
2. Leave the amount at **$50** and tap **Create allowance**. You are sent to PayPal.
3. Log in with the sandbox buyer from the Devpost instructions and **approve**. You land on **/wallet** with $50 on hold.

*What you just saw:* the money is a PayPal authorization hold, not a payment.

## 2. The AI shopping agent (about 1 min)

Open **/agent** and send these one at a time:

| Say | Expect |
|---|---|
| `Get snacks for movie night under $15` | A cart and a **PASS** verdict card; the purchase is captured from the hold |
| `Get me a beer` | **BLOCKED**, with a plain-English reason (alcohol is on the blocked list) |
| `Book a city ride` | Allowed (transport); if it is above your "ask me" threshold you get **NEEDS_APPROVAL** and can approve it on **/wallet** |
| `Return the chips` | A refund of that item, reported separately (refunds go to your PayPal balance, not back onto the hold) |
| `How much do I have left?` | Remaining on hold, spent, refunded |

The model proposes; a deterministic Policy Judge decides. Try asking for something absurd, it will not get through.

## 3. Offline payment on one laptop (about 2 min)

This simulates a buyer phone and a merchant phone with two browser windows.

1. **Buyer window:** open `/wallet/?dev=1`, tap **Show to Pay**, then **Copy code**. (This is the exact text inside the QR.)
2. **Merchant window:** open `/merchant/?dev=1` in a **second window**, pick a merchant (e.g. *Chai Corner*).
3. In the merchant window open DevTools (F12) → **Network** tab → set throttling to **Offline**. The page shows an **OFFLINE** badge. (Do this for the buyer window too if you like; the wallet QR is generated locally.)
4. In **Dev: paste the QR JSON** paste the code, tap **Use pasted code**, type an amount at or below the per-tap limit (e.g. `3`) and charge. You get a green local verification: signatures, serial, limit and category were all checked **with no network**.
5. **Replay it:** paste the same code again and charge. It is **rejected** (serial already used).
6. Set the network back to **No throttling**. The merchant page **syncs automatically**, the server re-verifies, captures from the hold and pays the merchant through PayPal Payouts. You see a settlement report with an AI risk note.
7. Back in the buyer window **/wallet** shows the new spend and the reduced balance.

*What you just saw:* a payment verified with zero internet, backed by the PayPal hold, settled later with no double charge possible.

## 4. The dashboard (about 30 s)

Open **/dashboard**: KPIs (held, spent, returned, vouchers), the **Audit log** (the **Decisions only** toggle is on by default: each verdict with its reasons; untick it for all agent chatter), and filters for actor, verdict and allowance. AG Grid tables for vouchers and purchases sit below.

## 5. Optional: bring your own agent (MCP) with Claude Desktop

On **/wallet**, under **AI agents on your leash**, create an agent key (optionally set a per-purchase cap) and follow [MCP.md](MCP.md) to connect Claude Desktop. Ask it to buy chips (works), a beer (blocked) and to refund; the dashboard logs each as actor `external_agent`. Revoke the key and it stops immediately.

## If something looks off

- A blank wallet usually means the API was still waking; reload after a few seconds.
- Owner actions (approve, close, agent keys) work only in the browser that created the allowance, by design (no login, owner token stored on that device). Use one browser profile for the whole test.
- Everything is PayPal **Sandbox**, USD.
