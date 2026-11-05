# Deploying LeashPay (Render + Supabase)

Two free Render web services (API + web, Singapore) and a Supabase Postgres database. Sandbox PayPal only.

## 0. Before you start
- Supabase project created; copy the **Session pooler** connection string (Project → Connect → Session pooler):
  `postgresql://postgres.<ref>:<password>@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres`
  - If the password has special characters (`@ : / # ?`), URL-encode them (`@` → `%40`).
- Generate the signing key once and **keep it** (changing it invalidates issued allowance certificates):
  `cd backend && python scripts/gen_signing_key.py`
- Code pushed to GitHub (`main`).

## 1. Deploy the Blueprint
1. Render dashboard → **New +** → **Blueprint** → connect the GitHub repo → branch `main`.
2. Render reads `render.yaml` and proposes `leashpay-api` and `leashpay-web`. It asks for every `sync: false` value.
3. Fill the **API** values now; for the two URL values that don't exist yet, put a placeholder (`https://placeholder.invalid`), you fix them in step 3. Leave `NEXT_PUBLIC_API_URL` as a placeholder too.
4. **Apply**. The API builds first (~3–5 min).

## 2. Which variable goes where

| Variable | Service | Value |
|---|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | api | Sandbox REST app credentials |
| `DATABASE_URL` | api | Supabase **Session pooler** URL (never commit it) |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | api | LLM keys (Groq is the fallback) |
| `MERCHANT_PAYOUT_EMAIL` | api | Sandbox business account email that receives payouts |
| `SERVER_SIGNING_KEY` | api | Output of `scripts/gen_signing_key.py` (**required**: the API refuses to start in production without it) |
| `FRONTEND_URL` | api | Web URL, e.g. `https://leashpay-web.onrender.com` (PayPal return redirect) |
| `CORS_ORIGINS` | api | Same web URL (comma-separate several; no trailing slash needed) |
| `APP_ENV`, `LLM_PROVIDER`, `LLM_FALLBACK` | api | Preset in `render.yaml` |
| `NEXT_PUBLIC_API_URL` | **web** | API URL, e.g. `https://leashpay-api.onrender.com` (**build-time**, see below) |

## 3. Wire the two services together (the order matters)
1. Open **leashpay-api** → copy its URL (`https://leashpay-api-xxxx.onrender.com`).
2. **leashpay-web** → Environment → `NEXT_PUBLIC_API_URL` = the API URL.
3. Open **leashpay-web** → copy its URL.
4. **leashpay-api** → Environment → `FRONTEND_URL` and `CORS_ORIGINS` = the web URL (exactly, `https://…`, no path).
5. **Redeploy both** (Manual Deploy → *Deploy latest commit*; for web choose *Clear build cache & deploy*).
   `NEXT_PUBLIC_API_URL` is inlined into the JS bundle at build time, so the web service **must be rebuilt** whenever it changes. A plain restart won't pick it up.

## 4. Verify
1. `https://<api>/health` → `{"status":"ok","db":"postgresql"}` (the first hit after idle takes ~30–50 s).
2. `https://<api>/merchants` → the 3 seeded demo merchants (seed runs on first boot).
3. Open the web URL → **Pay** → `/setup`: type rules → parsed policy → amount → **Approve with PayPal** → sandbox buyer approves → returns to `/setup/return` → `/wallet` shows the allowance and a QR.
4. `/agent`: "Snacks for movie night under $8" shows a Policy Judge verdict.
5. `/dashboard` shows the audit log; rows in Supabase → Table Editor confirm Postgres is in use.
If the page says *Waking up the server…* that is the free tier cold start; it retries automatically.

## 5. Keep it awake (optional, free)
Render free services sleep after ~15 min idle. UptimeRobot → **Add New Monitor** → type *HTTP(s)*, URL `https://<api>/health`, interval **5 minutes**. (Optionally add one for the web URL.)

## Notes / limits
- Free tier: cold starts; the PWA's offline mode works from the installed/cached app regardless.
- The Supabase **Session pooler** (port 5432) is used so SQLAlchemy's connection pool works; the app keeps ≤10 connections.
- PayPal sandbox app: no redirect-URL allow-list is needed for Orders v2; the return URL comes from `FRONTEND_URL`.
- Free-tier disks are ephemeral; nothing is stored on disk because `SERVER_SIGNING_KEY` and the DB live in env/Supabase.
