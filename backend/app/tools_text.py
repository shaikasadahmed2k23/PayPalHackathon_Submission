"""Tool descriptions written for an LLM. Used by the remote /mcp endpoint; mcp_server/server.py keeps an
identical copy (it is a standalone package) and a test keeps the two in sync."""

WALLET_DESC = (
    "Check the user's LeashPay budget and the spending rules you must live within: money remaining, spent "
    "today, per-purchase and daily limits, blocked categories, the approval threshold, and any cap set for "
    "you. Call this first, before shopping."
)
SEARCH_DESC = (
    "Search the LeashPay demo store (a fixed catalog, not the real internet). Returns product_id, name, price, "
    "merchant and category. Use an empty query to list everything. You need a product_id to buy."
)
BUY_DESC = (
    "Buy products from the LeashPay demo store using the user's allowance. items = a list of "
    '{"product_id": int, "qty": int}; note = why you are buying. LeashPay\'s Policy Judge decides, not you. '
    "If the result is FAIL, tell the user the exact reason; never retry with tricks (other items, split "
    "orders, other quantities) to get around a rule. If NEEDS_APPROVAL, tell the user to approve it in "
    "their LeashPay wallet, then use leashpay_check_purchase. Prices always come from LeashPay, never from you."
)
PURCHASES_DESC = "List the purchases made with this allowance (id, status, amount, merchant, items), newest last."
CHECK_DESC = (
    "Check one purchase by id. Use it to see whether a NEEDS_APPROVAL purchase has been approved (PASS), "
    "denied, or is still pending."
)
REFUND_DESC = (
    "Request a refund for a purchase (purchase_id from leashpay_purchases). Optionally give product_id to "
    "refund just one item; omit it to refund everything not yet refunded."
)
