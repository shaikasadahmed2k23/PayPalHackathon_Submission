from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db import init_db
from app.paypal import PayPalError
from app.routers import allowance, approvals, crypto, policy


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="LeashPay", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(policy.router)
app.include_router(crypto.router)
app.include_router(allowance.router)
app.include_router(approvals.router)


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
def health() -> dict[str, str]:
    return {"status": "ok"}
