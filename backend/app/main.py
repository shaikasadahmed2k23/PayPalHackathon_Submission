import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import check_production_settings, get_settings
from app.db import check_db, engine, init_db
from app.seed import seed_if_empty
from app.paypal import PayPalError
from app.mcp_http import mcp
from app.routers import (
    agent, agent_api, agent_keys, allowance, approvals, audit, crypto, merchants, policy, vouchers,
)
from sqlmodel import Session
from starlette.types import ASGIApp, Receive, Scope, Send

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    check_production_settings(get_settings())
    init_db()
    with Session(engine) as session:
        seed_if_empty(session)
    async with mcp.session_manager.run():  # the remote MCP endpoint at /mcp
        yield


app = FastAPI(title="LeashPay", lifespan=lifespan)


class AgentKeyGuard:
    """A request that carries an agent key may only use /agent-api and /mcp. So an external agent can
    never reach /approvals, /policy or /allowance (close, keys, ...) even if it knows the URLs."""

    ALLOWED = ("/agent-api", "/mcp", "/health")

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] != "OPTIONS":
            h = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            has_key = "x-agent-key" in h or h.get("authorization", "").lower().startswith("bearer lpk_")
            if has_key and not scope["path"].startswith(self.ALLOWED):
                body = {"detail": "Agent keys can only use /agent-api and /mcp. Approvals, rules and "
                                  "closing the allowance are for the user."}
                await JSONResponse(body, status_code=403)(scope, receive, send)
                return
        await self.inner(scope, receive, send)


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AgentKeyGuard)


app.include_router(policy.router)
app.include_router(crypto.router)
app.include_router(allowance.router)
app.include_router(approvals.router)
app.include_router(audit.router)
app.include_router(merchants.router)
app.include_router(vouchers.router)
app.include_router(agent.router)
app.include_router(agent_keys.router)
app.include_router(agent_api.router)
app.router.routes.extend(r for r in mcp.streamable_http_app().routes if getattr(r, "path", "") == "/mcp")


@app.exception_handler(PayPalError)
async def paypal_error_handler(_: Request, exc: PayPalError) -> JSONResponse:
    """Surface PayPal's exact error instead of a bare 500."""
    return JSONResponse(
        status_code=502,
        content={"detail": {
            "paypal_status": exc.status, "name": exc.name, "message": exc.message,
            "details": exc.details, "debug_id": exc.debug_id,
        }},
    )


@app.get("/health")
def health() -> JSONResponse:
    """Render's health check: 200 only if the database answers. Reports the dialect, never the URL."""
    try:
        return JSONResponse({"status": "ok", "db": check_db()})
    except Exception:
        logging.getLogger(__name__).exception("health check: database unreachable")
        return JSONResponse({"status": "error", "db": "unreachable"}, status_code=503)
