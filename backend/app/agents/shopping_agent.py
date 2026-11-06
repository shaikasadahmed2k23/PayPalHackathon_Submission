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
from app.models import Allowance, Merchant, Product, Purchase, PurchaseStatus
from app.services.purchase import (  # noqa: F401  (re-exported: routers and tests import them from here)
    PurchaseOut, RefundOut, SkippedOut, buy_items, line_names as _names, list_purchases, money as _m,
    purchase_out, refund_purchase, remaining_qty as _remaining,
)
from app.services.spend import get_spend_context

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


# ---- buy ------------------------------------------------------------------------------

async def _buy(
    session: Session, allowance: Allowance, message: str, turn: Turn, candidates: list[dict[str, Any]]
) -> AgentReply:
    catalog = {c["id"]: c for c in candidates}

    # Prices come from the DB (buy_items); the LLM contributes product ids and quantities only.
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

    result = await buy_items(session, allowance.id, [{"product_id": i, "qty": wanted[i]} for i in ids],  # type: ignore[arg-type]
                             actor="shopping_agent", note=message, rationale=turn.rationale)
    skipped += result.skipped
    lines_out = list(result.lines)
    if trimmed:
        n = len(trimmed)
        lines_out.append(f"Kept it under {_m(turn.budget)} - left out {n} extra{'s' if n > 1 else ''} "
                         f"({', '.join(reversed(trimmed))}).")
    for s in skipped:
        if s.item not in trimmed:  # rule-blocked groups stay as separate lines
            lines_out.append(f"I skipped {s.item}: {'; '.join(s.reasons)}.")
    lines_out.append(f"Total charged now: {_m(result.charged)}.")
    return AgentReply(conversation_id="", reply=" ".join(lines_out), intent="buy",
                      purchases=result.purchases, skipped=skipped)


# ---- refund ---------------------------------------------------------------------------

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
        res = await refund_purchase(session, allowance, purchase_id, product_ids, actor="shopping_agent")
        if res.refund:
            refunds.append(res.refund)
        else:
            notes.append(res.note)

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
