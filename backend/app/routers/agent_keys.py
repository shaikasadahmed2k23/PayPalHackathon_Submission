"""Owner-side management of external-agent keys (the user's wallet UI calls these)."""

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.models import AgentKey, Allowance
from app.services import agent_api as svc
from app.services.owner import OwnerHeader, check_owner

router = APIRouter(tags=["agent-keys"])


class KeyIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=60)]
    per_purchase_cap: Optional[Annotated[float, Field(gt=0)]] = None


class KeyOut(BaseModel):
    id: int
    allowance_id: int
    name: str
    key_prefix: str
    per_purchase_cap: Optional[float]
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]


class KeyCreated(KeyOut):
    key: str  # the full key: returned ONCE


def _out(k: AgentKey) -> KeyOut:
    return KeyOut(id=k.id, allowance_id=k.allowance_id, name=k.name, key_prefix=k.key_prefix,  # type: ignore[arg-type]
                  per_purchase_cap=k.per_purchase_cap, created_at=k.created_at,
                  last_used_at=k.last_used_at, revoked_at=k.revoked_at)


@router.post("/allowance/{allowance_id}/agent-keys")
def create_key(allowance_id: int, body: KeyIn, session: Session = Depends(get_session),
               x_owner_token: OwnerHeader = None) -> KeyCreated:
    if session.get(Allowance, allowance_id) is None:
        raise HTTPException(404, f"Allowance {allowance_id} not found")
    check_owner(session, allowance_id, x_owner_token)
    row, raw = svc.create_key(session, allowance_id, body.name, body.per_purchase_cap)
    return KeyCreated(**_out(row).model_dump(), key=raw)


@router.get("/allowance/{allowance_id}/agent-keys")
def list_keys(allowance_id: int, session: Session = Depends(get_session),
              x_owner_token: OwnerHeader = None) -> list[KeyOut]:
    check_owner(session, allowance_id, x_owner_token)
    rows = session.exec(select(AgentKey).where(AgentKey.allowance_id == allowance_id).order_by(AgentKey.id)).all()  # type: ignore[arg-type]
    return [_out(k) for k in rows]


@router.delete("/agent-keys/{key_id}")
def revoke_key(key_id: int, session: Session = Depends(get_session),
               x_owner_token: OwnerHeader = None) -> KeyOut:
    row = session.get(AgentKey, key_id)
    if row is None:
        raise HTTPException(404, f"Agent key {key_id} not found")
    check_owner(session, row.allowance_id, x_owner_token)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        session.refresh(row)
    return _out(row)
