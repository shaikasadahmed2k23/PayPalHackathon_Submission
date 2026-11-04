"""Retry merchant payouts for captured vouchers stuck in SETTLED_PAYOUT_PENDING.

Usage (server running):  python scripts/retry_payouts.py <allowance_id>
"""

import os
import sys

import httpx

API = os.environ.get("BACKEND_URL", "http://localhost:8000")


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("usage: python scripts/retry_payouts.py <allowance_id>")
    r = httpx.post(f"{API}/vouchers/retry-payouts", params={"allowance_id": sys.argv[1]}, timeout=90)
    if r.is_error:
        sys.exit(f"{r.status_code}\n{r.text}")
    report = r.json()
    print(f"retried={report['retried']} settled={report['settled']} still_pending={report['still_pending']}")
    for it in report["items"]:
        print(f"  serial {it['serial']}  ${it['amount']:.2f}  {it['status']}  payout={it['payout_batch_id']}")
        for reason in it["reasons"]:
            print("      -", reason)


if __name__ == "__main__":
    main()
