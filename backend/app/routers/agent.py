from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.agents import shopping_agent
from app.agents.shopping_agent import AgentReply, PurchaseOut
from app.db import get_session

router = APIRouter(tags=["agent"])


class ChatIn(BaseModel):
    allowance_id: int
    message: str
    conversation_id: Optional[str] = None


@router.post("/agent/chat")
async def chat(body: ChatIn, session: Session = Depends(get_session)) -> AgentReply:
    if not body.message.strip():
        raise HTTPException(422, "message must not be empty")
    try:
        return await shopping_agent.handle(session, body.allowance_id, body.message, body.conversation_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/purchases")
def purchases(allowance_id: int, session: Session = Depends(get_session)) -> list[PurchaseOut]:
    return shopping_agent.list_purchases(session, allowance_id)
