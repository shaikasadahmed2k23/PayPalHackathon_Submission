"""Remote MCP endpoint (Streamable HTTP) at /mcp. Same six tools as mcp_server/server.py, but they call
the agent services directly. Auth: `Authorization: Bearer lpk_...` (or X-Agent-Key) on every request."""

import inspect
from typing import Any, Callable, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlmodel import Session

from app.db import engine
from app.routers.agent_api import key_from_headers
from app.services import agent_api as svc
from app.tools_text import BUY_DESC, CHECK_DESC, PURCHASES_DESC, REFUND_DESC, SEARCH_DESC, WALLET_DESC

# stateless + JSON responses: no server-side sessions to lose when a free-tier instance sleeps.
# DNS-rebinding protection only allows localhost Hosts, which would reject onrender.com. It guards
# cookie-less localhost servers; here every request needs a bearer key, so it is switched off.
mcp = FastMCP(
    "LeashPay", stateless_http=True, json_response=True, streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _raw_key(ctx: Context) -> Optional[str]:
    req = ctx.request_context.request
    h = req.headers if req is not None else {}
    return key_from_headers(h.get("x-agent-key"), h.get("authorization"))


async def _run(ctx: Context, fn: Callable[..., Any]) -> str:
    """Authenticate this request's key, run fn(session, key, allowance), return the text for the LLM."""
    with Session(engine) as session:
        try:
            key, allowance = svc.authenticate(session, _raw_key(ctx))
            out = fn(session, key, allowance)
            if inspect.isawaitable(out):
                out = await out
        except svc.AgentAuthError as e:
            return f"ERROR: {e.detail}"
        except (LookupError, ValueError) as e:
            return f"ERROR: {e}"
        return out["text"]


@mcp.tool(name="leashpay_wallet", description=WALLET_DESC)
async def leashpay_wallet(ctx: Context) -> str:
    return await _run(ctx, lambda s, k, a: svc.wallet(s, k, a))


@mcp.tool(name="leashpay_search_store", description=SEARCH_DESC)
async def leashpay_search_store(query: str, ctx: Context) -> str:
    return await _run(ctx, lambda s, k, a: svc.products(s, query))


@mcp.tool(name="leashpay_buy", description=BUY_DESC)
async def leashpay_buy(items: list[dict[str, int]], ctx: Context, note: str = "") -> str:
    clean = [{"product_id": int(i["product_id"]), "qty": max(1, min(int(i.get("qty", 1)), 5))} for i in items[:10]]
    return await _run(ctx, lambda s, k, a: svc.buy(s, k, a, clean, note))


@mcp.tool(name="leashpay_purchases", description=PURCHASES_DESC)
async def leashpay_purchases(ctx: Context) -> str:
    return await _run(ctx, lambda s, k, a: svc.purchases(s, a))


@mcp.tool(name="leashpay_check_purchase", description=CHECK_DESC)
async def leashpay_check_purchase(purchase_id: int, ctx: Context) -> str:
    return await _run(ctx, lambda s, k, a: svc.purchase(s, a, purchase_id))


@mcp.tool(name="leashpay_refund", description=REFUND_DESC)
async def leashpay_refund(purchase_id: int, ctx: Context, product_id: Optional[int] = None) -> str:
    return await _run(ctx, lambda s, k, a: svc.refund(s, k, a, purchase_id, product_id))
