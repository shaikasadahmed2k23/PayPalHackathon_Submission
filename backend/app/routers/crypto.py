from fastapi import APIRouter

from app import crypto

router = APIRouter(prefix="/crypto", tags=["crypto"])


@router.get("/server-pubkey")
def server_pubkey() -> dict[str, str]:
    return {"pubkey_b64": crypto.server_pubkey_b64()}
