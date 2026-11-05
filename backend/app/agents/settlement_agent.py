"""Settlement Agent: turns a merchant's synced offline payments into captures + payouts.

Deterministic checks and the spend engine decide every money movement. The LLM only
reviews the finished batch for anomalies and never changes a decision.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import audit
from app.llm import generate_json
from app.models import Allowance, Merchant, Voucher, VoucherStatus
from app.paypal import PayPalError, get_paypal_client
from app.services.spend import make_request_id, spend
from app.services.voucher_verify import (
    OfflinePayment,
    is_authentic,
    parse_ts,
    verify_offline_payment,
)

logger = logging.getLogger("leashpay.settlement")
REPLAY_REASON = "Voucher already used (possible screenshot replay)"
ALREADY_SETTLED = "Already settled earlier"


class ReportItem(BaseModel):
    serial: Optional[int]
    amount: float
    status: str  # SETTLED | SETTLED_PAYOUT_PENDING | REJECTED
    reasons: list[str] = Field(default_factory=list)
    capture_id: Optional[str] = None
    payout_batch_id: Optional[str] = None
    already: bool = False  # settled by an earlier sync of the same payment; nothing new happened


class AIReview(BaseModel):
    risk: Literal["low", "medium", "high", "unknown"]
    findings: list[str] = Field(default_factory=list)


class _LLMReview(BaseModel):
    risk: Literal["low", "medium", "high"]
    findings: list[str] = Field(default_factory=list)


class SettlementReport(BaseModel):
    settled: int  # newly captured from the hold (payout may still be pending)
    already_settled: int = 0  # re-sent payments that an earlier sync had already settled
    rejected: int
    payout_pending: int
    total_settled_amount: float
    items: list[ReportItem]
    ai_review: AIReview
    summary: str


_REVIEW_SYSTEM = """You review a batch of offline payment vouchers that a merchant just synced.
Each item has: serial, amount, issued_at, scanned_at, merchant, status.
Statuses were already decided by deterministic rules; you only look for suspicious patterns,
such as many vouchers in a short window, repeated or replayed serials, amounts sitting right at
the cap, a long gap between issued_at and scanned_at, or repeated rejections.
You cannot approve or block anything. Return JSON: {"risk": "low"|"medium"|"high", "findings": [short plain-English sentences]}.
Use an empty findings list and "low" when nothing looks unusual."""


def _serial(p: OfflinePayment) -> Optional[int]:
    s = p.voucher.get("serial")
    return s if isinstance(s, int) and not isinstance(s, bool) else None


def _opt_ts(value: str) -> Optional[datetime]:
    try:
        return parse_ts(value)
    except ValueError:
        return None


def _find_voucher(session: Session, allowance_id: int, serial: int) -> Optional[Voucher]:
    return session.exec(
        select(Voucher).where(Voucher.allowance_id == allowance_id, Voucher.serial == serial)
    ).first()


def _new_voucher(merchant: Merchant, p: OfflinePayment, aid: int, serial: int) -> Voucher:
    return Voucher(
        allowance_id=aid, serial=serial, merchant_id=merchant.id, amount=p.amount,
        signature=p.device_signature, issued_at=parse_ts(p.voucher["issued_at"]),
        scanned_at=_opt_ts(p.scanned_at),
    )


def _save_new(session: Session, voucher: Voucher) -> bool:
    """Insert; False if UNIQUE(allowance_id, serial) says it already exists."""
    session.add(voucher)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    session.refresh(voucher)
    return True


def _reject(
    session: Session, merchant: Merchant, p: OfflinePayment, reasons: list[str], voucher: Optional[Voucher] = None
) -> ReportItem:
    if voucher is not None:
        voucher.status = VoucherStatus.REJECTED
        voucher.judge_reasons = reasons
        session.add(voucher)
        session.commit()
    item = ReportItem(serial=_serial(p), amount=p.amount, status="REJECTED", reasons=reasons)
    _audit_item(session, merchant, p, item)
    return item


def _audit_item(session: Session, merchant: Merchant, p: OfflinePayment, item: ReportItem) -> None:
    audit.log(
        session, "settlement_agent", "settle_voucher",
        verdict=item.status, reasons=item.reasons,
        paypal_ref=item.payout_batch_id or item.capture_id,
        payload={
            "allowance_id": p.voucher.get("allowance_id"), "serial": item.serial,
            "amount": p.amount, "merchant_id": merchant.id, "merchant": merchant.name,
            "capture_id": item.capture_id, "payout_batch_id": item.payout_batch_id,
        },
    )


async def _pay_out(merchant: Merchant, voucher: Voucher, request_id: str) -> Optional[str]:
    """Pay the merchant for a captured voucher; sets voucher status/payout fields.
    Returns an error message, or None on success. request_id is the idempotent sender_batch_id."""
    try:
        if not merchant.paypal_email:
            raise PayPalError(0, "Merchant has no PayPal email — set MERCHANT_PAYOUT_EMAIL")
        batch_id, _ = await get_paypal_client().payout(
            merchant.paypal_email, voucher.amount, sender_batch_id=request_id,
            note=f"LeashPay voucher {voucher.allowance_id}-{voucher.serial} at {merchant.name}",
        )
    except PayPalError as e:
        error = e.message if e.status == 0 else str(e)
        voucher.status = VoucherStatus.SETTLED_PAYOUT_PENDING
        voucher.judge_reasons = [f"Payout failed: {error}"]
        logger.warning("Payout for voucher %s failed: %s", request_id, error)
        return error
    voucher.status = VoucherStatus.SETTLED
    voucher.payout_batch_id = batch_id
    voucher.judge_reasons = []
    return None


async def _settle_one(session: Session, merchant: Merchant, p: OfflinePayment) -> ReportItem:
    reasons: list[str] = []
    if p.merchant_id != merchant.id:
        reasons.append("This payment was made to a different merchant")
    reasons += verify_offline_payment(p, merchant)

    serial, aid = _serial(p), p.voucher.get("allowance_id")

    # Idempotent re-sync: the identical, authentic payment (same merchant, same amount) that an
    # earlier sync already settled is acknowledged, not rejected. Anything else stays a replay.
    if serial is not None and isinstance(aid, int) and is_authentic(p) and p.merchant_id == merchant.id:
        prior = _find_voucher(session, aid, serial)
        if (prior and prior.merchant_id == merchant.id and prior.amount == p.amount
                and prior.status in (VoucherStatus.SETTLED, VoucherStatus.SETTLED_PAYOUT_PENDING)):
            item = ReportItem(serial=serial, amount=p.amount, status=prior.status.value, already=True,
                              reasons=[ALREADY_SETTLED], capture_id=prior.capture_id,
                              payout_batch_id=prior.payout_batch_id)
            _audit_item(session, merchant, p, item)
            return item

    # Only authentic vouchers (both signatures valid) may occupy an (allowance, serial) slot.
    storable = (
        is_authentic(p) and serial is not None and isinstance(aid, int)
        and p.merchant_id == merchant.id and session.get(Allowance, aid) is not None
    )
    if is_authentic(p) and not storable and not reasons:
        reasons.append("Unknown allowance")
    if reasons:
        # An existing row for this serial must not be overwritten: only record if unused.
        row = None
        if storable and _find_voucher(session, aid, serial) is None:  # type: ignore[arg-type]
            candidate = _new_voucher(merchant, p, aid, serial)  # type: ignore[arg-type]
            row = candidate if _save_new(session, candidate) else None
        return _reject(session, merchant, p, reasons, row)

    assert serial is not None and isinstance(aid, int)
    voucher = _new_voucher(merchant, p, aid, serial)
    if _find_voucher(session, aid, serial) is not None or not _save_new(session, voucher):
        return _reject(session, merchant, p, [REPLAY_REASON])

    allowance = session.get(Allowance, aid)
    assert allowance is not None  # `storable` checked it exists
    request_id = make_request_id(allowance, "voucher", serial)
    try:
        result = await spend(
            session, aid, p.amount, merchant.category, merchant.name, "offline",
            source="voucher", source_id=str(voucher.id), request_id=request_id,
            skip_approval=True,  # per_tap_max IS the offline approval limit
        )
    except PayPalError as e:
        # Nothing was captured. Drop the row so the merchant can sync this voucher again.
        session.delete(voucher)
        session.commit()
        return _reject(session, merchant, p, [f"PayPal capture failed, sync again later: {e}"])

    if result.verdict.decision != "PASS" or not result.capture_id:
        return _reject(session, merchant, p, result.verdict.reasons, voucher)

    voucher.capture_id = result.capture_id
    session.add(voucher)
    session.commit()  # money is captured: record it before attempting the payout
    item = ReportItem(serial=serial, amount=p.amount, status="SETTLED", capture_id=result.capture_id)
    error = await _pay_out(merchant, voucher, request_id)
    if error:
        # Keep the capture on record; the merchant is still owed this money.
        item.status = "SETTLED_PAYOUT_PENDING"
        item.reasons = voucher.judge_reasons
    item.payout_batch_id = voucher.payout_batch_id
    session.add(voucher)
    session.commit()
    _audit_item(session, merchant, p, item)
    return item


async def _ai_review(
    session: Session, merchant: Merchant, payments: list[OfflinePayment], items: list[ReportItem]
) -> AIReview:
    batch: list[dict[str, Any]] = [
        {
            "serial": it.serial, "amount": it.amount, "issued_at": p.voucher.get("issued_at"),
            "scanned_at": p.scanned_at, "merchant": merchant.name, "status": it.status,
        }
        for p, it in zip(payments, items)
    ]
    try:
        r = await asyncio.to_thread(generate_json, _REVIEW_SYSTEM, json.dumps(batch), _LLMReview)
        review, note = AIReview(risk=r.risk, findings=r.findings), r.findings
    except Exception as e:  # advisory only: nothing here may break settlement
        logger.warning("AI review failed: %s: %s", type(e).__name__, e)
        review, note = AIReview(risk="unknown"), [f"AI review unavailable: {type(e).__name__}: {e}"]
    audit.log(
        session, "settlement_agent", "anomaly_review", verdict=review.risk, reasons=note,
        payload={"merchant_id": merchant.id, "vouchers": len(batch)},
    )
    return review


def _summary(merchant: Merchant, report: SettlementReport) -> str:
    total = len(report.items)
    text = (
        f"Settled {report.settled} of {total} vouchers for {merchant.name} "
        f"(${report.total_settled_amount:,.2f}); {report.rejected} rejected."
    )
    if report.already_settled:
        text += f" {report.already_settled} had already been settled earlier."
    if report.payout_pending:
        text += f" {report.payout_pending} captured but the merchant payout is still pending."
    if report.ai_review.risk in ("medium", "high"):
        text += f" AI review flagged {report.ai_review.risk} risk."
    return text


async def settle(session: Session, merchant_id: int, payments: list[OfflinePayment]) -> SettlementReport:
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise LookupError(f"Merchant {merchant_id} not found")
    items = [await _settle_one(session, merchant, p) for p in payments]
    paid = ("SETTLED", "SETTLED_PAYOUT_PENDING")
    already = [i for i in items if i.already]
    captured = [i for i in items if i.status in paid and not i.already]
    ai = await _ai_review(session, merchant, payments, items) if payments else AIReview(risk="low")
    report = SettlementReport(
        settled=len(captured),
        already_settled=len(already),
        rejected=len(items) - len(captured) - len(already),
        payout_pending=sum(i.status == "SETTLED_PAYOUT_PENDING" for i in items if not i.already),
        total_settled_amount=round(sum(i.amount for i in captured), 2),
        items=items, ai_review=ai, summary="",
    )
    report.summary = _summary(merchant, report)
    return report


class RetryReport(BaseModel):
    retried: int
    settled: int
    still_pending: int
    items: list[ReportItem]


async def retry_payouts(session: Session, allowance_id: Optional[int] = None) -> RetryReport:
    """Retry payouts for captured vouchers. Same sender_batch_id as the first attempt,
    so PayPal treats a repeat as the same payout rather than a second one."""
    q = select(Voucher).where(Voucher.status == VoucherStatus.SETTLED_PAYOUT_PENDING)
    if allowance_id is not None:
        q = q.where(Voucher.allowance_id == allowance_id)
    items: list[ReportItem] = []
    for voucher in session.exec(q.order_by(Voucher.id)).all():  # type: ignore[arg-type]
        merchant = session.get(Merchant, voucher.merchant_id)
        allowance = session.get(Allowance, voucher.allowance_id)
        if merchant is None or allowance is None:
            continue
        error = await _pay_out(merchant, voucher, make_request_id(allowance, "voucher", voucher.serial))
        session.add(voucher)
        session.commit()
        item = ReportItem(
            serial=voucher.serial, amount=voucher.amount, status=voucher.status.value,
            reasons=voucher.judge_reasons, capture_id=voucher.capture_id,
            payout_batch_id=voucher.payout_batch_id,
        )
        audit.log(
            session, "settlement_agent", "retry_payout", verdict=item.status,
            reasons=[error] if error else [], paypal_ref=item.payout_batch_id or item.capture_id,
            payload={"allowance_id": voucher.allowance_id, "serial": voucher.serial,
                     "merchant_id": merchant.id},
        )
        items.append(item)
    still = sum(i.status == "SETTLED_PAYOUT_PENDING" for i in items)
    return RetryReport(retried=len(items), settled=len(items) - still, still_pending=still, items=items)
