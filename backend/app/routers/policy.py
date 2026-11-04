from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app import audit
from app.db import get_session
from app.llm import LLMError
from app.models import Policy as PolicyRow
from app.policy.judge import judge
from app.policy.parser import ParsedPolicy, parse_rules
from app.policy.schema import Policy, SpendContext, SpendRequest, Verdict

router = APIRouter(prefix="/policy", tags=["policy"])


class ParseIn(BaseModel):
    text: str


class SaveIn(BaseModel):
    raw_text: str
    policy: Policy


class PolicyOut(BaseModel):
    id: int
    raw_text: str
    policy: Policy
    created_at: datetime


class JudgeIn(BaseModel):
    spend: SpendRequest
    context: SpendContext


def _get_row(session: Session, policy_id: int) -> PolicyRow:
    row = session.get(PolicyRow, policy_id)
    if not row:
        raise HTTPException(404, f"Policy {policy_id} not found")
    return row


@router.post("/parse")
def parse(body: ParseIn) -> ParsedPolicy:
    if not body.text.strip():
        raise HTTPException(422, "text must not be empty")
    try:
        return parse_rules(body.text)
    except LLMError as e:
        raise HTTPException(502, f"Could not parse rules: {e}") from e


@router.post("")
def save(body: SaveIn, session: Session = Depends(get_session)) -> dict[str, int]:
    row = PolicyRow(raw_text=body.raw_text, policy_json=body.policy.model_dump(mode="json"))
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.id is not None
    return {"id": row.id}


@router.get("/{policy_id}")
def get_policy(policy_id: int, session: Session = Depends(get_session)) -> PolicyOut:
    row = _get_row(session, policy_id)
    assert row.id is not None
    return PolicyOut(
        id=row.id,
        raw_text=row.raw_text,
        policy=Policy.model_validate(row.policy_json),
        created_at=row.created_at,
    )


@router.post("/{policy_id}/judge")
def judge_spend(
    policy_id: int, body: JudgeIn, session: Session = Depends(get_session)
) -> Verdict:
    row = _get_row(session, policy_id)
    verdict = judge(Policy.model_validate(row.policy_json), body.spend, body.context)
    payload: dict[str, Any] = {
        "policy_id": policy_id,
        "spend": body.spend.model_dump(mode="json"),
        "context": body.context.model_dump(mode="json"),
        "checks": [c.model_dump() for c in verdict.checks],
    }
    audit.log(
        session,
        "policy_judge",
        "judge_spend",
        verdict=verdict.decision,
        reasons=verdict.reasons,
        payload=payload,
    )
    return verdict
