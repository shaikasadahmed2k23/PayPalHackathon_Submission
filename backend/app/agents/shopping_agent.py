"""Shopping Agent (Mode A): chat -> cart -> judge -> spend(), plus refunds and status.

The LLM only understands the request and picks product ids + quantities. Prices, categories,
merchants and every money decision come from the DB, the Policy Judge and spend().
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Literal, Optional, TypeVar

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app import audit
from app.llm import LLMError, generate_json
from app.models import (
    Allowance, Approval, LedgerEntry, Merchant, Policy as PolicyRow, Product, Purchase,
    PurchaseStatus,
)
from app.paypal import PayPalError, get_paypal_client
from app.policy.judge import judge
from app.policy.schema import Policy, SpendRequest
from app.services.spend import (
    allowance_lock, get_spend_context, make_request_id, spend,
)

logger = logging.getLogger("leashpay.shopping")
T = TypeVar("T", bound=BaseModel)

HISTORY_TURNS = 10
_history: dict[str, list[dict[str, str]]] = {}


# ---- LLM schema (ONE structured call per user turn) -----------------------------------------

class PlanItem(BaseModel):
    product_id: int
    qty: int = 1


class RefundRef(BaseModel):
    purchase_id: int
    product_id: int = 0  # 0 = every item of that purchase not yet refunded


class Turn(BaseModel):
    intent: Literal["buy", "refund", "status", "chitchat"]
    budget: float = 0.0  # buy: max total in USD the user gave, 0 = none
    items: list[PlanItem] = Field(default_factory=list)  # buy: products picked from the candidates
    refund_refs: list[RefundRef] = Field(default_factory=list)  # refund: from refundable_purchases
    rationale: str = ""
    reply: str = ""  # chitchat only: one or two friendly sentences


# ---- API output -----------------------------------------------------------------------

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


class AgentReply(BaseModel):
    conversation_id: str
    reply: str
    intent: str
    purchases: list[PurchaseOut] = Field(default_factory=list)
    skipped: list[SkippedOut] = Field(default_factory=list)
    refunds: list[RefundOut] = Field(default_factory=list)


# ---- prompts --------------------------------------------------------------------------

TURN_SYSTEM = """You are the brain of LeashPay's shopping assistant. For ONE user message, return JSON:
{"intent": "buy"|"refund"|"status"|"chitchat", "budget": number, "items": [{"product_id": int, "qty": int}],
 "refund_refs": [{"purchase_id": int, "product_id": int}], "rationale": str, "reply": str}

You are given: the message, recent conversation, `candidates` (the store catalog; keyword_match=true
means it matched the message) and `refundable_purchases` (what the user already bought).

- buy: the user wants to purchase or book something. Pick what the user asked for from `candidates`;
  use ONLY product_id values from the list, never invent products, ids or prices. Rules are enforced
  separately by the system - do not filter, refuse or substitute because of any rules. If a candidate
  is a reasonable match for what they asked, pick it (qty 1 unless they said otherwise). Leave items
  empty only if nothing in the catalog resembles the request. budget = the maximum total in USD if
  they gave one, else 0.
- refund: the user wants to return something or get money back. Fill refund_refs using ONLY ids from
  `refundable_purchases`. product_id = 0 means every unrefunded item of that purchase; otherwise the
  item's product_id. If it is unclear which purchase they mean, leave refund_refs empty.
- status: asks about spending, balance, what was bought or what is left.
- chitchat: anything else; put a short friendly reply (1-2 sentences, no claims of money moving) in `reply`.
Use the recent conversation to resolve words like "it" or "that"."""


# ---- helpers --------------------------------------------------------------------------

def _m(x: float) -> str:
    return f"${x:,.2f}"


async def _llm(schema: type[T], system: str, payload: dict[str, Any]) -> T:
    return await asyncio.to_thread(generate_json, system, json.dumps(payload), schema)


def _remember(cid: str, user: str, reply: str) -> None:
    turns = _history.setdefault(cid, [])
    turns += [{"role": "user", "text": user}, {"role": "assistant", "text": reply}]
    del turns[: -2 * HISTORY_TURNS]


def reset_conversations() -> None:
    _history.clear()


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if len(w) >= 3}


_STOP = {"the", "and", "for", "get", "also", "grab", "some", "any", "with", "under", "keep",
         "that", "this", "please", "can", "you", "buy", "want", "need", "and", "all", "pack"}


def search_products(session: Session, text: str) -> list[dict[str, Any]]:
    """Keyword + category + merchant match. Matches come first; the rest of the (tiny) demo
    catalog follows so the LLM always has options."""
    words = _tokens(text) - _STOP
    merchants = {m.id: m for m in session.exec(select(Merchant)).all()}
    rows = []
    for p in session.exec(select(Product).order_by(Product.id)).all():  # type: ignore[arg-type]
        m = merchants.get(p.merchant_id)
        if m is None:
            continue
        hit = bool(words & _tokens(f"{p.name} {p.category} {m.name}"))
        rows.append({"id": p.id, "name": p.name, "price": p.price, "category": m.category,
                     "merchant": m.name, "keyword_match": hit})
    return sorted(rows, key=lambda r: not r["keyword_match"])


_REFUND_WORDS = re.compile(r"\b(return|refund|money back|send (?:it )?back|take (?:it )?back)\b", re.I)
_STATUS_WORDS = re.compile(r"\b(spent|spend|spending|remaining|balance|how much|left|total)\b", re.I)
_BUY_WORDS = re.compile(r"\b(buy|get|grab|book|order|purchase|pick up|want)\b", re.I)


def keyword_intent(message: str) -> str:
    """Used only when every LLM provider fails."""
    if _REFUND_WORDS.search(message):
        return "refund"
    if _STATUS_WORDS.search(message):
        return "status"
    if _BUY_WORDS.search(message):
        return "buy"
    return "chitchat"


def _refs_from_keywords(message: str, recent: list[Purchase]) -> list["RefundRef"]:
    """No LLM: refund the item(s) whose name shares a word with the message (newest purchase first)."""
    words = _tokens(message) - _STOP - {"return", "refund", "back", "money", "send", "take"}
    for p in recent:
        refs = [RefundRef(purchase_id=p.id, product_id=l["product_id"])  # type: ignore[arg-type]
                for l in p.items_json if _remaining(l) > 0 and words & _tokens(l["name"])]
        if refs:
            return refs
    return []


def _names(lines: list[dict[str, Any]]) -> str:
    return ", ".join(f"{l['qty']}x {l['name']}" if l["qty"] > 1 else l["name"] for l in lines)


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


# ---- buy ------------------------------------------------------------------------------

async def _buy(
    session: Session, allowance: Allowance, message: str, turn: Turn, candidates: list[dict[str, Any]]
) -> AgentReply:
    catalog = {c["id"]: c for c in candidates}

    # Prices come from the DB; the LLM contributes product ids and quantities only.
    wanted: dict[int, int] = {}
    for it in turn.items:
        if it.product_id in catalog:  # hallucinated ids are dropped
            wanted[it.product_id] = min(wanted.get(it.product_id, 0) + max(it.qty, 1), 5)
        else:
            logger.warning("Ignoring unknown product_id %s from the LLM", it.product_id)

    skipped: list[SkippedOut] = []
    ids = list(wanted)

    def total(keys: list[int]) -> float:
        return round(sum(catalog[i]["price"] * wanted[i] for i in keys), 2)

    trimmed: list[str] = []  # dropped to fit the user's budget; reported as one line
    while turn.budget > 0 and ids and total(ids) > turn.budget:
        dropped = ids.pop()
        trimmed.append(catalog[dropped]["name"])
        skipped.append(SkippedOut(item=catalog[dropped]["name"],
                                  reasons=[f"It would put the cart over your {_m(turn.budget)} budget"]))
    if not ids:
        text = "I couldn't find anything in the store that fits that request."
        if skipped:
            text = "Everything I found was over your budget, so I bought nothing."
        return AgentReply(conversation_id="", reply=text, intent="buy", skipped=skipped)

    groups: dict[str, list[dict[str, Any]]] = {}
    for pid in ids:
        c = catalog[pid]
        groups.setdefault(c["merchant"], []).append({
            "product_id": pid, "name": c["name"], "qty": wanted[pid], "unit_price": c["price"],
            "line_total": round(c["price"] * wanted[pid], 2), "refunded_qty": 0,
        })
    merchants = {m.name: m for m in session.exec(select(Merchant)).all()}
    policy = Policy.model_validate(session.get(PolicyRow, allowance.policy_id).policy_json)  # type: ignore[union-attr]
    ctx = get_spend_context(session, allowance)
    audit.log(session, "shopping_agent", "propose_cart", payload={
        "allowance_id": allowance.id, "message": message, "rationale": turn.rationale,
        "groups": {k: v for k, v in groups.items()}})

    purchases: list[PurchaseOut] = []
    lines_out: list[str] = []
    charged = 0.0
    for merchant_name, lines in groups.items():
        merchant = merchants[merchant_name]
        for l in lines:
            l["merchant"], l["merchant_id"] = merchant.name, merchant.id
        amount = round(sum(l["line_total"] for l in lines), 2)
        desc = _names(lines)
        purchase = Purchase(allowance_id=allowance.id, items_json=lines, amount=amount,  # type: ignore[arg-type]
                            status=PurchaseStatus.BLOCKED)
        session.add(purchase)
        session.commit()
        session.refresh(purchase)

        # Dry run: skip a group that would FAIL instead of attempting it.
        dry = judge(policy, SpendRequest(amount=amount, category=merchant.category,  # type: ignore[arg-type]
                                         merchant_name=merchant.name, channel="online"), ctx)
        if dry.decision == "FAIL":
            purchase.judge_reasons = dry.reasons
            session.add(purchase)
            session.commit()
            audit.log(session, "shopping_agent", "skip_group", verdict="FAIL", reasons=dry.reasons,
                      payload={"purchase_id": purchase.id, "merchant": merchant.name, "amount": amount})
            skipped.append(SkippedOut(item=f"{merchant.name} ({desc})", reasons=dry.reasons))
            purchases.append(purchase_out(session, purchase))
            continue

        try:
            result = await spend(
                session, allowance.id, amount, merchant.category, merchant.name, "online",  # type: ignore[arg-type]
                source="purchase", source_id=str(purchase.id),
                request_id=make_request_id(allowance, "purchase", purchase.id),
            )
        except PayPalError as e:
            purchase.judge_reasons = [f"PayPal capture failed: {e}"]
            session.add(purchase)
            session.commit()
            skipped.append(SkippedOut(item=f"{merchant.name} ({desc})", reasons=purchase.judge_reasons))
            purchases.append(purchase_out(session, purchase))
            continue

        session.refresh(purchase)
        session.refresh(allowance)
        purchase.judge_reasons = result.verdict.reasons
        if result.verdict.decision == "PASS":
            purchase.status, purchase.capture_id = PurchaseStatus.CAPTURED, result.capture_id
            charged += amount
            ctx = get_spend_context(session, allowance)  # later groups see this spend
            lines_out.append(f"Bought {desc} from {merchant.name} for {_m(amount)}.")
        elif result.verdict.decision == "NEEDS_APPROVAL":
            purchase.status = PurchaseStatus.PENDING_APPROVAL
            lines_out.append(
                f"{desc} from {merchant.name} comes to {_m(amount)}, above your approval limit - "
                f"I've asked for your approval (nothing charged yet).")
        else:
            skipped.append(SkippedOut(item=f"{merchant.name} ({desc})", reasons=result.verdict.reasons))
        session.add(purchase)
        session.commit()
        purchases.append(purchase_out(session, purchase))

    if trimmed:
        n = len(trimmed)
        lines_out.append(f"Kept it under {_m(turn.budget)} - left out {n} extra{'s' if n > 1 else ''} "
                         f"({', '.join(reversed(trimmed))}).")
    for s in skipped:
        if s.item not in trimmed:  # rule-blocked groups stay as separate lines
            lines_out.append(f"I skipped {s.item}: {'; '.join(s.reasons)}.")
    lines_out.append(f"Total charged now: {_m(charged)}.")
    return AgentReply(conversation_id="", reply=" ".join(lines_out), intent="buy",
                      purchases=purchases, skipped=skipped)


# ---- refund ---------------------------------------------------------------------------

def _remaining(line: dict[str, Any]) -> int:
    return int(line["qty"]) - int(line.get("refunded_qty", 0))


async def _refund(session: Session, allowance: Allowance, refs: list[RefundRef], recent: list[Purchase]) -> AgentReply:
    if not recent:
        return AgentReply(conversation_id="", reply="You don't have any purchases I can refund yet.", intent="refund")
    valid = {p.id: p for p in recent}
    wanted: dict[int, set[int]] = {}  # purchase_id -> product_ids (empty set = all unrefunded)
    for ref in refs:
        p = valid.get(ref.purchase_id)
        if p is None or (ref.product_id and ref.product_id not in {l["product_id"] for l in p.items_json}):
            logger.warning("Ignoring unknown refund ref %s", ref)
            continue
        if ref.product_id == 0:
            wanted[p.id] = set()  # type: ignore[index]
        elif wanted.get(p.id, {-1}) != set():  # type: ignore[arg-type]
            wanted.setdefault(p.id, set()).add(ref.product_id)  # type: ignore[arg-type]
    if not wanted:
        return AgentReply(conversation_id="", intent="refund",
                          reply="I'm not sure which purchase you mean - could you name the item you'd like to return?")

    refunds: list[RefundOut] = []
    notes: list[str] = []
    for purchase_id, product_ids in wanted.items():
        async with allowance_lock(allowance.id):  # type: ignore[arg-type]
            purchase = session.get(Purchase, purchase_id)
            assert purchase is not None
            session.refresh(purchase)
            todo = [l for l in purchase.items_json
                    if _remaining(l) > 0 and (not product_ids or l["product_id"] in product_ids)]
            if not todo or not purchase.capture_id:
                notes.append("That was already refunded." if not todo else "That purchase has no payment to refund.")
                continue
            amount = round(sum(l["unit_price"] * _remaining(l) for l in todo), 2)
            key = f"{purchase.id}-" + "+".join(str(i) for i in sorted(l["product_id"] for l in todo))
            try:
                refund_id, status = await get_paypal_client().refund(
                    purchase.capture_id, amount, make_request_id(allowance, "refund", key))
            except PayPalError as e:
                audit.log(session, "shopping_agent", "refund_failed", reasons=[str(e)],
                          payload={"purchase_id": purchase.id, "amount": amount})
                notes.append(f"PayPal couldn't process the refund of {_m(amount)}: {e}")
                continue

            done = {l["product_id"] for l in todo}
            purchase.items_json = [
                {**l, "refunded_qty": l["qty"]} if l["product_id"] in done else l for l in purchase.items_json
            ]  # new list so SQLAlchemy sees the JSON change
            full = all(_remaining(l) == 0 for l in purchase.items_json)
            purchase.status = PurchaseStatus.REFUNDED if full else PurchaseStatus.PARTIALLY_REFUNDED
            entry = session.exec(select(LedgerEntry).where(
                LedgerEntry.source == "purchase", LedgerEntry.source_id == str(purchase.id))).first()
            if entry:
                entry.refunded_amount = round(entry.refunded_amount + amount, 2)
                session.add(entry)
            session.add(purchase)
            session.commit()
            audit.log(session, "shopping_agent", "refund", verdict=purchase.status.value, paypal_ref=refund_id,
                      payload={"purchase_id": purchase.id, "amount": amount, "status": status,
                               "items": [l["name"] for l in todo]})
            refunds.append(RefundOut(purchase_id=purchase.id, amount=amount, refund_id=refund_id,  # type: ignore[arg-type]
                                     status=status, items=[l["name"] for l in todo]))

    parts = [f"Refunded {_m(r.amount)} for {', '.join(r.items)} (PayPal refund {r.status.lower()})." for r in refunds]
    parts += notes
    if refunds:
        parts.append(f"Total refunded: {_m(sum(r.amount for r in refunds))}.")
    return AgentReply(conversation_id="", reply=" ".join(parts), intent="refund", refunds=refunds,
                      purchases=[purchase_out(session, session.get(Purchase, pid)) for pid in wanted])  # type: ignore[arg-type]


# ---- status ----------------------------------------------------------------------------

def _status(session: Session, allowance: Allowance) -> AgentReply:
    """Built entirely from the ledger in code; never depends on an LLM."""
    ctx = get_spend_context(session, allowance)
    rows = list_purchases(session, allowance.id)  # type: ignore[arg-type]
    pending = [r for r in rows if r.status == "PENDING_APPROVAL"]
    last = [r for r in rows if r.status != "BLOCKED"][-5:]
    reply = (f"You've spent {_m(ctx.spent_today)} today and have {_m(ctx.allowance_remaining)} left "
             f"of your {_m(allowance.amount)} allowance.")
    if last:
        reply += " Recent purchases: " + "; ".join(
            f"{r.merchant} {_m(r.amount)} ({r.status.lower().replace('_', ' ')})" for r in last) + "."
    if pending:
        reply += f" {len(pending)} waiting for your approval."
    return AgentReply(conversation_id="", reply=reply, intent="status", purchases=last)


# ---- entry point -----------------------------------------------------------------------

def _refundable(session: Session, allowance_id: int) -> list[Purchase]:
    return list(session.exec(
        select(Purchase).where(
            Purchase.allowance_id == allowance_id,
            Purchase.status.in_([PurchaseStatus.CAPTURED, PurchaseStatus.PARTIALLY_REFUNDED]),  # type: ignore[attr-defined]
        ).order_by(Purchase.id.desc()).limit(10)  # type: ignore[attr-defined]
    ).all())


async def handle(
    session: Session, allowance_id: int, message: str, conversation_id: Optional[str] = None
) -> AgentReply:
    allowance = session.get(Allowance, allowance_id)
    if allowance is None:
        raise LookupError(f"Allowance {allowance_id} not found")
    cid = conversation_id or uuid.uuid4().hex
    history = list(_history.get(cid, []))
    candidates = search_products(session, message)
    recent = _refundable(session, allowance_id)

    turn: Optional[Turn] = None
    try:
        turn = await _llm(Turn, TURN_SYSTEM, {
            "message": message, "history": history[-6:], "candidates": candidates,
            "refundable_purchases": [
                {"purchase_id": p.id, "merchant": (p.items_json or [{}])[0].get("merchant"),
                 "amount": p.amount, "status": p.status.value,
                 "items": [{"product_id": l["product_id"], "name": l["name"], "qty": l["qty"],
                            "refunded_qty": l.get("refunded_qty", 0)} for l in p.items_json]}
                for p in recent],
        })
    except LLMError as e:
        logger.warning("Shopping agent LLM unavailable, using keyword fallback: %s", e)
    intent = turn.intent if turn else keyword_intent(message)
    audit.log(session, "shopping_agent", "intent", verdict=intent,
              payload={"allowance_id": allowance_id, "message": message, "via": "llm" if turn else "keywords"})

    if intent == "status":
        out = _status(session, allowance)
    elif intent == "refund":
        refs = turn.refund_refs if turn else _refs_from_keywords(message, recent)
        out = await _refund(session, allowance, refs, recent)
    elif intent == "buy" and turn:
        out = await _buy(session, allowance, message, turn, candidates)
    elif intent == "buy":
        out = AgentReply(conversation_id="", intent="buy", reply=(
            "I can't reach my language model right now, so I can't pick products safely. "
            "I haven't spent anything - please try again in a minute."))
    else:
        reply = turn.reply if turn and turn.reply else (
            "Hi! I can buy things from the demo store within your rules, refund purchases, "
            "or tell you how much you've spent.")
        out = AgentReply(conversation_id="", intent="chitchat", reply=reply)
    out.conversation_id = cid
    _remember(cid, message, out.reply)
    return out
