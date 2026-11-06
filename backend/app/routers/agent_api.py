"""REST API for external AI agents. Auth: `X-Agent-Key: lpk_...` (or `Authorization: Bearer lpk_...`).

An agent key unlocks ONLY these routes (see AgentKeyGuard in main.py): no policies, no approvals,
no closing the allowance."""

from dataclasses import dataclass
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_session
from app.models import AgentKey, Allowance
from app.services import agent_api as svc

router = APIRouter(prefix="/agent-api", tags=["agent-api"])


@dataclass
class Auth:
    key: AgentKey
    allowance: Allowance


def key_from_headers(x_agent_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_agent_key:
        return x_agent_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


def current_agent(
    x_agent_key: Annotated[Optional[str], Header()] = None,
    authorization: Annotated[Optional[str], Header()] = None,
    session: Session = Depends(get_session),
) -> Auth:
    try:
        key, allowance = svc.authenticate(session, key_from_headers(x_agent_key, authorization))
    except svc.AgentAuthError as e:
        raise HTTPException(e.status, e.detail) from e
    return Auth(key=key, allowance=allowance)


class ItemIn(BaseModel):
    product_id: int
    qty: Annotated[int, Field(ge=1, le=5)] = 1


class BuyIn(BaseModel):
    items: Annotated[list[ItemIn], Field(min_length=1, max_length=10)]
    note: str = ""


class RefundIn(BaseModel):
    purchase_id: int
    product_id: Optional[int] = None


@router.get("/wallet")
def wallet(a: Auth = Depends(current_agent), session: Session = Depends(get_session)) -> dict[str, Any]:
    return svc.wallet(session, a.key, a.allowance)


@router.get("/products")
def products(q: str = "", a: Auth = Depends(current_agent), session: Session = Depends(get_session)) -> dict[str, Any]:
    return svc.products(session, q)


@router.post("/buy")
async def buy(body: BuyIn, a: Auth = Depends(current_agent), session: Session = Depends(get_session)) -> dict[str, Any]:
    return await svc.buy(session, a.key, a.allowance, [i.model_dump() for i in body.items], body.note)


@router.get("/purchases")
def purchases(a: Auth = Depends(current_agent), session: Session = Depends(get_session)) -> dict[str, Any]:
    return svc.purchases(session, a.allowance)


@router.get("/purchases/{purchase_id}")
def purchase(purchase_id: int, a: Auth = Depends(current_agent), session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return svc.purchase(session, a.allowance, purchase_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/refund")
async def refund(body: RefundIn, a: Auth = Depends(current_agent), session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        return await svc.refund(session, a.key, a.allowance, body.purchase_id, body.product_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
