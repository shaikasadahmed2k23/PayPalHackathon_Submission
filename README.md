# LeashPay

A PayPal wallet with a leash: spending rules in plain English, enforced by an AI agent, online and offline.
See [CLAUDE.md](CLAUDE.md) for the full spec. (This README is a stub; more sections come later.)

## Notes: offline vouchers and closing an allowance

- **The hold is the guarantee.** An allowance is a PayPal authorization for the full amount. Offline
  vouchers are bounded by `max_vouchers x per_tap_max <= allowance amount`, so merchants can always be
  paid from the hold when they sync, even days later.
- **Late sync.** A voucher issued before the certificate expired can still settle after the allowance
  expires (72h), as long as the allowance is still `ACTIVE`. Online and manual spends stay strictly
  time-limited. PayPal itself only honors an authorization for about 29 days, so merchants must sync
  within that window.
- **Closing early forfeits unsynced vouchers.** Closing voids the authorization, releasing everything
  not yet captured. The server cannot see vouchers that merchants have not synced, so
  `POST /allowance/{id}/close` returns `409` while any voucher serial is unaccounted for
  (see `GET /allowance/{id}/offline-status`). Pass `?force=true` to close anyway; merchants holding
  unsynced vouchers then cannot be paid.
- **Idempotency keys** are `voucher-{paypal_authorization_id}-{serial}` (also the payout
  `sender_batch_id`), so they stay unique across database resets and are identical on retry.
