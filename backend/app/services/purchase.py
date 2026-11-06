"""The deterministic buy / refund path shared by the chat shopping agent and external agents (REST + MCP).

Whoever decides *what* to buy (our LLM, Claude Desktop, ...) only supplies product ids and quantities.
Prices, merchants and categories come from the DB; every cent goes through the Policy Judge and spend().
"""

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app import audit
from app.models import (
    AgentKey, Allowance, Approval, LedgerEntry, Merchant, Policy as PolicyRow, Product, Purchase,
    PurchaseStatus,
)
from app.paypal import PayPalError, get_paypal_client
from app.policy.judge import judge
from app.policy.schema import Policy, SpendRequest
from app.services.spend import allowance_lock, compute_refunds, get_spend_context, make_request_id, spend

logger = logging.getLogger("leashpay.purchase")

MAX_QTY = 5  # per product per request


# ---- output models -------------------------------------------------------------------------

class PurchaseOut(BaseModel):
    id: int
    status: str
    amount: float
    merchant: str
    merchant_id: Optional[int] = None
    items: list[dict[str, Any]]
    capture_id: Optional[str] = None
    approval_id: Optional[int] = None
    reasons: list[str] = Field(default_factory=list)


class SkippedOut(BaseModel):
    item: str
    reasons: list[str]


class RefundOut(BaseModel):
    purchase_id: int
    amount: float
    refund_id: str
    status: str
    items: list[str]


class BuyResult(BaseModel):
    purchases: list[PurchaseOut] = Field(default_factory=list)
    skipped: list[SkippedOut] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)  # one plain-English sentence per merchant group
    charged: float = 0.0


class RefundResult(BaseModel):
    refund: Optional[RefundOut] = None
    note: str = ""  # why nothing was refunded / what went wrong


# ---- helpers -------------------------------------------------------------------------------

def money(x: float) -> str:
    return f"${x:,.2f}"


def balance_line(session: Session, allowance: Allowance) -> str:
    """The one wording of the balance, used by the wallet text, the chat agent and the MCP tools.
    Spent money never returns to the PayPal hold: refunds go to the buyer's PayPal balance."""
    session.refresh(allowance)
    ctx = get_spend_context(session, allowance)
    refunded, _ = compute_refunds(session, allowance.id)  # type: ignore[arg-type]
    text = (f"Left on hold: {money(ctx.allowance_remaining)} of {money(allowance.amount)}. "
            f"Spent: {money(allowance.captured_total)} ({money(ctx.spent_today)} today).")
    if refunded > 0:
        text += f" Refunded: {money(refunded)} (returned to your PayPal balance - refunds don't refill the hold)."
    return text


def refund_text(amount: float, remaining: float) -> str:
    return (f"Refunded {money(amount)} to your PayPal balance. Your hold stays at {money(remaining)} "
            "because spent money can't return to a PayPal hold.")


def line_names(lines: list[dict[str, Any]]) -> str:
    return ", ".join(f"{l['qty']}x {l['name']}" if l["qty"] > 1 else l["name"] for l in lines)


def remaining_qty(line: dict[str, Any]) -> int:
    return int(line["qty"]) - int(line.get("refunded_qty", 0))


def purchase_out(session: Session, p: Purchase) -> PurchaseOut:
    approval = session.exec(
        select(Approval).where(Approval.source == "purchase", Approval.source_id == str(p.id))
    ).first()
    first = p.items_json[0] if p.items_json else {}
    return PurchaseOut(
        id=p.id, status=p.status.value, amount=p.amount,  # type: ignore[arg-type]
        merchant=first.get("merchant", ""), merchant_id=first.get("merchant_id"),
        items=p.items_json, capture_id=p.capture_id, approval_id=approval.id if approval else None,
        reasons=p.judge_reasons,
    )


def list_purchases(session: Session, allowance_id: int) -> list[PurchaseOut]:
    rows = session.exec(select(Purchase).where(Purchase.allowance_id == allowance_id).order_by(Purchase.id)).all()  # type: ignore[arg-type]
    return [purchase_out(session, p) for p in rows]


def _agent_info(agent_key: Optional[AgentKey]) -> dict[str, Any]:
    return {"agent": agent_key.name, "key_prefix": agent_key.key_prefix} if agent_key else {}


# ---- buy -----------------------------------------------------------------------------------

async def buy_items(
    session: Session,
    allowance_id: int,
    items: list[dict[str, int]],
    actor: str = "shopping_agent",
    agent_key: Optional[AgentKey] = None,
    note: str = "",
    rationale: str = "",
) -> BuyResult:
    """items = [{"product_id": int, "qty": int}]. Groups by merchant, dry-runs the judge, then spend().

    One Purchase row per merchant group (BLOCKED rows are kept so the user can see what was refused)."""
    allowance = session.get(Allowance, allowance_id)
    if allowance is None:
        raise LookupError(f"Allowance {allowance_id} not found")

    skipped: list[SkippedOut] = []
    wanted: dict[int, int] = {}
    for it in items:
        pid = int(it["product_id"])
        wanted[pid] = min(wanted.get(pid, 0) + max(int(it.get("qty", 1)), 1), MAX_QTY)

    merchants = {m.id: m for m in session.exec(select(Merchant)).all()}
    groups: dict[int, list[dict[str, Any]]] = {}  # merchant_id -> lines
    for pid, qty in wanted.items():
        product = session.get(Product, pid)
        if product is None or product.merchant_id not in merchants:
            skipped.append(SkippedOut(item=f"Product {pid}", reasons=[f"Product {pid} is not in the store"]))
            continue
        groups.setdefault(product.merchant_id, []).append({
            "product_id": pid, "name": product.name, "qty": qty, "unit_price": product.price,
            "line_total": round(product.price * qty, 2), "refunded_qty": 0,
            "merchant": merchants[product.merchant_id].name, "merchant_id": product.merchant_id,
        })
    if not groups:
        return BuyResult(skipped=skipped)

    policy = Policy.model_validate(session.get(PolicyRow, allowance.policy_id).policy_json)  # type: ignore[union-attr]
    ctx = get_spend_context(session, allowance)
    who = _agent_info(agent_key)
    audit.log(session, actor, "propose_cart", payload={
        "allowance_id": allowance_id, "message": note, "rationale": rationale, **who,
        "groups": {merchants[mid].name: lines for mid, lines in groups.items()}})

    result = BuyResult(skipped=skipped)
    for merchant_id, lines in groups.items():
        merchant = merchants[merchant_id]
        amount = round(sum(l["line_total"] for l in lines), 2)
        desc = line_names(lines)
        purchase = Purchase(allowance_id=allowance_id, items_json=lines, amount=amount,
                            status=PurchaseStatus.BLOCKED)
        session.add(purchase)
        session.commit()
        session.refresh(purchase)

        # Dry run (the judge also applies this agent's own cap): skip a group that would FAIL.
        dry = judge(policy, SpendRequest(amount=amount, category=merchant.category,  # type: ignore[arg-type]
                                         merchant_name=merchant.name, channel="online"), ctx,
                    agent_cap=agent_key.per_purchase_cap if agent_key else None,
                    agent_name=agent_key.name if agent_key else "the agent")
        reasons = dry.reasons if dry.decision == "FAIL" else []
        if reasons:
            purchase.judge_reasons = reasons
            session.add(purchase)
            session.commit()
            audit.log(session, actor, "skip_group", verdict="FAIL", reasons=reasons,
                      payload={"allowance_id": allowance_id, "purchase_id": purchase.id,
                               "merchant": merchant.name, "amount": amount, **who})
            result.skipped.append(SkippedOut(item=f"{merchant.name} ({desc})", reasons=reasons))
            result.purchases.append(purchase_out(session, purchase))
            continue

        try:
            spent = await spend(
                session, allowance_id, amount, merchant.category, merchant.name, "online",
                source="purchase", source_id=str(purchase.id),
                request_id=make_request_id(allowance, "purchase", purchase.id),
                audit_extra=who,
            )
        except PayPalError as e:
            purchase.judge_reasons = [f"PayPal capture failed: {e}"]
            session.add(purchase)
            session.commit()
            result.skipped.append(SkippedOut(item=f"{merchant.name} ({desc})", reasons=purchase.judge_reasons))
            result.purchases.append(purchase_out(session, purchase))
            continue

        session.refresh(purchase)
        session.refresh(allowance)
        purchase.judge_reasons = spent.verdict.reasons
        if spent.verdict.decision == "PASS":
            purchase.status, purchase.capture_id = PurchaseStatus.CAPTURED, spent.capture_id
            result.charged += amount
            ctx = get_spend_context(session, allowance)  # later groups see this spend
            result.lines.append(f"Bought {desc} from {merchant.name} for {money(amount)}.")
        elif spent.verdict.decision == "NEEDS_APPROVAL":
            purchase.status = PurchaseStatus.PENDING_APPROVAL
            result.lines.append(
                f"{desc} from {merchant.name} comes to {money(amount)}, above your approval limit - "
                f"I've asked for your approval (nothing charged yet).")
        else:
            result.skipped.append(SkippedOut(item=f"{merchant.name} ({desc})", reasons=spent.verdict.reasons))
        session.add(purchase)
        session.commit()
        result.purchases.append(purchase_out(session, purchase))
    return result


# ---- refund --------------------------------------------------------------------------------

async def refund_purchase(
    session: Session,
    allowance: Allowance,
    purchase_id: int,
    product_ids: Optional[set[int]] = None,
    actor: str = "shopping_agent",
    agent_key: Optional[AgentKey] = None,
) -> RefundResult:
    """Refund the unrefunded items of one purchase (all of them when product_ids is empty/None)."""
    product_ids = product_ids or set()
    async with allowance_lock(allowance.id):  # type: ignore[arg-type]
        purchase = session.get(Purchase, purchase_id)
        if purchase is None or purchase.allowance_id != allowance.id:
            return RefundResult(note=f"Purchase {purchase_id} not found")
        session.refresh(purchase)
        todo = [l for l in purchase.items_json
                if remaining_qty(l) > 0 and (not product_ids or l["product_id"] in product_ids)]
        if not todo or not purchase.capture_id:
            return RefundResult(note="That was already refunded." if not todo
                                else "That purchase has no payment to refund.")
        amount = round(sum(l["unit_price"] * remaining_qty(l) for l in todo), 2)
        key = f"{purchase.id}-" + "+".join(str(i) for i in sorted(l["product_id"] for l in todo))
        who = _agent_info(agent_key)
        try:
            refund_id, status = await get_paypal_client().refund(
                purchase.capture_id, amount, make_request_id(allowance, "refund", key))
        except PayPalError as e:
            audit.log(session, actor, "refund_failed", reasons=[str(e)],
                      payload={"allowance_id": allowance.id, "purchase_id": purchase.id, "amount": amount, **who})
            return RefundResult(note=f"PayPal couldn't process the refund of {money(amount)}: {e}")

        done = {l["product_id"] for l in todo}
        purchase.items_json = [
            {**l, "refunded_qty": l["qty"]} if l["product_id"] in done else l for l in purchase.items_json
        ]  # new list so SQLAlchemy sees the JSON change
        full = all(remaining_qty(l) == 0 for l in purchase.items_json)
        purchase.status = PurchaseStatus.REFUNDED if full else PurchaseStatus.PARTIALLY_REFUNDED
        entry = session.exec(select(LedgerEntry).where(
            LedgerEntry.source == "purchase", LedgerEntry.source_id == str(purchase.id))).first()
        if entry:
            entry.refunded_amount = round(entry.refunded_amount + amount, 2)
            session.add(entry)
        session.add(purchase)
        session.commit()
        audit.log(session, actor, "refund", verdict=purchase.status.value, paypal_ref=refund_id,
                  payload={"allowance_id": allowance.id, "purchase_id": purchase.id, "amount": amount,
                           "status": status, "items": [l["name"] for l in todo], **who})
        return RefundResult(refund=RefundOut(
            purchase_id=purchase.id, amount=amount, refund_id=refund_id,  # type: ignore[arg-type]
            status=status, items=[l["name"] for l in todo]))
