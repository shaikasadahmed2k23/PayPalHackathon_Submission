"""Owner token: the credential that may run owner-only actions (authorize, approvals, close, keys...).

There is still no login. Creating an allowance returns an `lpo_` token ONCE; only its sha256 is stored.
Allowances created before this existed have no hash and stay usable (legacy). Agent keys never pass:
they are a different credential, and `AgentKeyGuard` also refuses them on these routes."""

import hashlib
import hmac
import secrets
from typing import Annotated, Optional

from fastapi import Header, HTTPException
from sqlmodel import Session

from app.models import Allowance

OWNER_PREFIX = "lpo_"
HEADER = "X-Owner-Token"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """(raw, sha256). raw is lpo_ + 32 url-safe chars; it is returned to the user once and never stored."""
    raw = OWNER_PREFIX + secrets.token_urlsafe(24)
    return raw, hash_token(raw)


def check_owner(session: Session, allowance_id: Optional[int], token: Optional[str]) -> None:
    """Passes for legacy allowances (no hash) and for the right token; else 401. For an unknown
    allowance it passes, so the route can report its own 404."""
    allowance = session.get(Allowance, allowance_id) if allowance_id is not None else None
    if allowance is None or not allowance.owner_token_hash:
        return
    if not token or not hmac.compare_digest(hash_token(token.strip()), allowance.owner_token_hash):
        raise HTTPException(401, "This action needs the allowance owner's token (X-Owner-Token). "
                                 "Use the device that created the allowance.")


OwnerHeader = Annotated[Optional[str], Header(alias=HEADER)]
