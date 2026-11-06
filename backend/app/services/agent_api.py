"""Everything an EXTERNAL agent (REST /agent-api or MCP) may do, in one place.

The external agent is the brain; LeashPay is the leash. It can look, search, buy, check and ask for
refunds. It can never change rules, raise limits, approve its own pending purchases or close the
allowance: nothing here (or reachable with an agent key) touches those.

Every function returns a plain dict that includes a short `text` the LLM can relay to the user.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from app.agents.shopping_agent import search_products
from app.models import AgentKey, Allowance, AllowanceStatus, Policy as PolicyRow, Purchase, PurchaseStatus
from app.policy.schema import Policy
from app.services.purchase import (
    PurchaseOut, buy_items, line_names, list_purchases, money, purchase_out, refund_purchase,
)
from app.services.spend import get_spend_context

KEY_PREFIX = "lpk_"
ACTOR = "external_agent"
VERDICT = {"CAPTURED": "PASS", "PARTIALLY_REFUNDED": "PASS", "REFUNDED": "PASS",
           "PENDING_APPROVAL": "NEEDS_APPROVAL", "BLOCKED": "FAIL"}


class AgentAuthError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


# ---- keys -----------------------------------------------------------------------------------

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_key(session: Session, allowance_id: int, name: str, cap: Optional[float]) -> tuple[AgentKey, str]:
    """Returns the row and the full key. The full key is never stored and is shown to the user once."""
    raw = KEY_PREFIX + secrets.token_urlsafe(24)  # 24 bytes -> exactly 32 url-safe chars
    row = AgentKey(allowance_id=allowance_id, name=name.strip(), key_prefix=raw[:8],
                   key_hash=hash_key(raw), per_purchase_cap=cap)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, raw


def authenticate(session: Session, raw: Optional[str]) -> tuple[AgentKey, Allowance]:
    """Valid, unrevoked key on an ACTIVE allowance, else AgentAuthError (401 / 403)."""
    raw = (raw or "").strip()
    row = session.exec(select(AgentKey).where(AgentKey.key_hash == hash_key(raw))).first() if raw else None
    if row is None:
        raise AgentAuthError(401, "Invalid agent key. Create one in the LeashPay wallet (AI agents on your leash).")
    if row.revoked_at is not None:
        raise AgentAuthError(401, f"The agent key for {row.name} was revoked by the user.")
    allowance = session.get(Allowance, row.allowance_id)
    if allowance is None or allowance.status != AllowanceStatus.ACTIVE:
        state = allowance.status.value.lower() if allowance else "missing"
        raise AgentAuthError(403, f"The allowance behind this key is {state}, so nothing can be bought.")
    row.last_used_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return row, allowance


# ---- reading --------------------------------------------------------------------------------

def rules_summary(policy: Policy) -> list[str]:
    rules = [
        f"Max {money(policy.per_tx_max)} per purchase",
        f"Max {money(policy.daily_max)} per day",
        f"Purchases above {money(policy.require_approval_above)} need the user's approval",
    ]
    if policy.allowed_categories:
        rules.append("Only these categories are allowed: " + ", ".join(policy.allowed_categories))
    if policy.blocked_categories:
        rules.append("Blocked categories: " + ", ".join(policy.blocked_categories))
    if policy.blocked_merchants:
        rules.append("Blocked merchants: " + ", ".join(policy.blocked_merchants))
    return rules


def wallet(session: Session, key: AgentKey, allowance: Allowance) -> dict[str, Any]:
    policy = Policy.model_validate(session.get(PolicyRow, allowance.policy_id).policy_json)  # type: ignore[union-attr]
    ctx = get_spend_context(session, allowance)
    rules = rules_summary(policy)
    if key.per_purchase_cap is not None:
        rules.append(f"Your own cap: {money(key.per_purchase_cap)} per purchase (set by the user for {key.name})")
    pending = [p for p in list_purchases(session, allowance.id) if p.status == "PENDING_APPROVAL"]  # type: ignore[arg-type]
    text = (f"Allowance remaining: {money(ctx.allowance_remaining)} of {money(allowance.amount)}. "
            f"Spent today: {money(ctx.spent_today)}. {len(pending)} purchase(s) waiting for the user's approval.\n"
            "Rules you must live within:\n" + "\n".join(f"- {r}" for r in rules))
    return {"allowance_id": allowance.id, "currency": policy.currency, "amount": allowance.amount,
            "remaining": ctx.allowance_remaining, "spent_today": ctx.spent_today, "rules": rules,
            "agent": {"name": key.name, "per_purchase_cap": key.per_purchase_cap},
            "pending_approvals": len(pending), "text": text}


def products(session: Session, q: str = "") -> dict[str, Any]:
    rows = search_products(session, q)
    if q.strip():
        rows = [r for r in rows if r["keyword_match"]]
    items = [{"product_id": r["id"], "name": r["name"], "price": r["price"],
              "category": r["category"], "merchant": r["merchant"]} for r in rows]
    text = ("No products matched. Try a broader word, or an empty query to list the whole store."
            if not items else
            "Store products (product_id | name | price | merchant | category):\n" + "\n".join(
                f"{i['product_id']} | {i['name']} | {money(i['price'])} | {i['merchant']} | {i['category']}"
                for i in items))
    return {"products": items, "text": text}


def _item_names(p: PurchaseOut) -> str:
    return line_names(p.items)


def _purchase_dict(p: PurchaseOut) -> dict[str, Any]:
    return {"purchase_id": p.id, "verdict": VERDICT.get(p.status, "FAIL"), "status": p.status,
            "merchant": p.merchant, "amount": p.amount, "items": _item_names(p), "reasons": p.reasons}


def _purchase_text(p: PurchaseOut) -> str:
    what = f"{_item_names(p)} from {p.merchant} for {money(p.amount)} (purchase #{p.id})"
    if p.status == "CAPTURED":
        return f"PASS: bought {what}."
    if p.status == "PENDING_APPROVAL":
        return (f"NEEDS_APPROVAL: {what} is above the user's approval limit. NOTHING was charged. Tell the user to "
                f"approve it in their LeashPay wallet, then call leashpay_check_purchase({p.id}) to see the outcome. "
                "You cannot approve it yourself.")
    if p.status in ("REFUNDED", "PARTIALLY_REFUNDED"):
        return f"{p.status}: {what}."
    if any(r.startswith("PayPal capture failed") for r in p.reasons):
        return (f"FAIL: {what} could not be paid: {'; '.join(p.reasons)}. NOTHING was charged. Tell the user; "
                "this is a payment-system error, not a rule, so a later retry may work.")
    return (f"FAIL: {what} was blocked. Reason: {'; '.join(p.reasons) or 'a rule failed'}. "
            "Tell the user this exact reason. Do not retry with tricks to get around the rule.")


def _remaining(session: Session, allowance: Allowance) -> float:
    session.refresh(allowance)
    return get_spend_context(session, allowance).allowance_remaining


async def buy(session: Session, key: AgentKey, allowance: Allowance, items: list[dict[str, int]],
              note: str = "") -> dict[str, Any]:
    res = await buy_items(session, allowance.id, items, actor=ACTOR, agent_key=key, note=note)  # type: ignore[arg-type]
    remaining = _remaining(session, allowance)
    lines = [_purchase_text(p) for p in res.purchases]
    lines += [f"FAIL: {s.item}. Reason: {'; '.join(s.reasons)}. Tell the user this exact reason."
              for s in res.skipped if not s.item.endswith(")")]  # unknown products; groups are in purchases
    if not lines:
        lines = ["Nothing was bought: none of those product_ids exist. Use leashpay_search_store first."]
    lines.append(f"Charged now: {money(res.charged)}. Allowance remaining: {money(remaining)}.")
    return {"purchases": [_purchase_dict(p) for p in res.purchases],
            "skipped": [s.model_dump() for s in res.skipped], "charged": round(res.charged, 2),
            "remaining": remaining, "text": "\n".join(lines)}


def purchases(session: Session, allowance: Allowance) -> dict[str, Any]:
    rows = list_purchases(session, allowance.id)  # type: ignore[arg-type]
    shown = [p for p in rows if p.status != "BLOCKED"][-20:]
    text = ("No purchases yet." if not shown else
            "Purchases (newest last):\n" + "\n".join(
                f"#{p.id} {p.status} {money(p.amount)} {p.merchant}: {_item_names(p)}" for p in shown))
    return {"purchases": [_purchase_dict(p) | {"approval_id": p.approval_id} for p in shown], "text": text}


def purchase(session: Session, allowance: Allowance, purchase_id: int) -> dict[str, Any]:
    row = session.get(Purchase, purchase_id)
    if row is None or row.allowance_id != allowance.id:
        raise LookupError(f"Purchase {purchase_id} not found")
    p = purchase_out(session, row)
    text = _purchase_text(p)
    if p.status == "PENDING_APPROVAL":
        text = f"STILL PENDING: {text}"
    return {**_purchase_dict(p), "text": text}


async def refund(session: Session, key: AgentKey, allowance: Allowance, purchase_id: int,
                 product_id: Optional[int] = None) -> dict[str, Any]:
    row = session.get(Purchase, purchase_id)
    if row is None or row.allowance_id != allowance.id:
        raise LookupError(f"Purchase {purchase_id} not found")
    if row.status not in (PurchaseStatus.CAPTURED, PurchaseStatus.PARTIALLY_REFUNDED):
        raise ValueError(f"Purchase {purchase_id} is {row.status.value}, so there is nothing to refund")
    if product_id and product_id not in {l["product_id"] for l in row.items_json}:
        raise ValueError(f"Purchase {purchase_id} has no product {product_id}")
    res = await refund_purchase(session, allowance, purchase_id, {product_id} if product_id else None,
                                actor=ACTOR, agent_key=key)
    if res.refund:
        r = res.refund
        text = (f"Refunded {money(r.amount)} for {', '.join(r.items)} (purchase #{r.purchase_id}). "
                f"Allowance remaining: {money(_remaining(session, allowance))}.")
        return {"refunded": True, "amount": r.amount, "refund_id": r.refund_id, "status": r.status, "text": text}
    return {"refunded": False, "amount": 0.0, "text": f"Nothing refunded: {res.note}"}
