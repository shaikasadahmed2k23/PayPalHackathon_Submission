"""THE single place money moves: judge -> capture / approval -> ledger -> audit."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel
from sqlmodel import Session, select

from app import audit
from app.models import Allowance, AllowanceStatus, Approval, LedgerEntry, Policy as PolicyRow
from app.paypal import PayPalError, get_paypal_client
from app.policy.judge import judge
from app.policy.schema import Check, Policy, SpendContext, SpendRequest, Verdict

_locks: dict[int, asyncio.Lock] = {}


class SpendResult(BaseModel):
    verdict: Verdict
    capture_id: Optional[str] = None
    approval_id: Optional[int] = None


def _lock_for(allowance_id: int) -> asyncio.Lock:
    return _locks.setdefault(allowance_id, asyncio.Lock())


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


def compute_spent_today(session: Session, allowance_id: int) -> float:
    """Net spend (captures minus refunds) since 00:00 UTC today."""
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    entries = session.exec(select(LedgerEntry).where(LedgerEntry.allowance_id == allowance_id)).all()
    return round(
        sum(e.amount - e.refunded_amount for e in entries if _as_utc(e.created_at) >= midnight), 2
    )


def _inactive_reason(a: Allowance) -> Optional[str]:
    if a.status != AllowanceStatus.ACTIVE:
        return f"This allowance is {a.status.value.lower()}, not active"
    if a.expires_at and _as_utc(a.expires_at) <= datetime.now(timezone.utc):
        return "This allowance has expired"
    if not a.authorization_id:
        return "This allowance has no PayPal authorization"
    return None


async def spend(
    session: Session,
    allowance_id: int,
    amount: float,
    category: str,
    merchant_name: str,
    channel: str,
    source: str,
    source_id: Optional[str],
    request_id: str,
    skip_approval: bool = False,
) -> SpendResult:
    """skip_approval=True is for spends the user already approved; FAIL rules still apply."""
    req = SpendRequest(
        amount=amount, category=category, merchant_name=merchant_name, channel=channel
    )
    async with _lock_for(allowance_id):
        allowance = session.get(Allowance, allowance_id)
        if allowance is None:
            raise LookupError(f"Allowance {allowance_id} not found")
        session.refresh(allowance)  # another request may have captured since we loaded it

        spent_today = compute_spent_today(session, allowance_id)
        context = SpendContext(
            spent_today=spent_today,
            allowance_remaining=max(round(allowance.amount - allowance.captured_total, 2), 0.0),
        )
        reason = _inactive_reason(allowance)
        if reason:
            verdict = Verdict(
                decision="FAIL",
                reasons=[reason],
                checks=[Check(rule="allowance_active", passed=False, detail=reason)],
            )
        else:
            policy_row = session.get(PolicyRow, allowance.policy_id)
            assert policy_row is not None
            policy = Policy.model_validate(policy_row.policy_json)
            verdict = judge(policy, req, context, skip_approval=skip_approval)

        result = SpendResult(verdict=verdict)
        payload: dict[str, Any] = {
            "allowance_id": allowance_id,
            "spend": req.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "source": source,
            "source_id": source_id,
            "checks": [c.model_dump() for c in verdict.checks],
        }

        if verdict.decision == "NEEDS_APPROVAL":
            approval = Approval(
                allowance_id=allowance_id,
                amount=amount,
                category=category,
                merchant_name=merchant_name,
                channel=channel,
                source=source,
                source_id=source_id,
            )
            session.add(approval)
            session.commit()
            session.refresh(approval)
            result.approval_id = approval.id
            payload["approval_id"] = approval.id

        audit.log(
            session, "policy_judge", "judge_spend",
            verdict=verdict.decision, reasons=verdict.reasons, payload=payload,
        )

        if verdict.decision == "PASS":
            assert allowance.authorization_id
            try:
                capture_id, status = await get_paypal_client().capture(
                    allowance.authorization_id, amount, final=False, request_id=request_id
                )
                if status not in ("COMPLETED", "PENDING"):
                    raise PayPalError(200, f"capture came back with status {status}")
            except PayPalError as e:
                audit.log(
                    session, "system", "capture_failed", reasons=[str(e)],
                    payload={**payload, "request_id": request_id},
                )
                raise
            session.add(
                LedgerEntry(
                    allowance_id=allowance_id,
                    amount=amount,
                    category=category,
                    merchant_name=merchant_name,
                    channel=channel,
                    source=source,
                    source_id=source_id,
                    capture_id=capture_id,
                )
            )
            allowance.captured_total = round(allowance.captured_total + amount, 2)
            session.add(allowance)
            session.commit()
            result.capture_id = capture_id
            audit.log(
                session, "system", "capture",
                verdict="PASS", paypal_ref=capture_id,
                payload={**payload, "capture_id": capture_id, "request_id": request_id, "status": status},
            )
        return result
