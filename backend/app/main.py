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
from app.routers import agent, allowance, approvals, audit, crypto, merchants, policy, vouchers
from sqlmodel import Session

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    check_production_settings(get_settings())
    init_db()
    with Session(engine) as session:
        seed_if_empty(session)
    yield


app = FastAPI(title="LeashPay", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(policy.router)
app.include_router(crypto.router)
app.include_router(allowance.router)
app.include_router(approvals.router)
app.include_router(audit.router)
app.include_router(merchants.router)
app.include_router(vouchers.router)
app.include_router(agent.router)


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
