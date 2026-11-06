"""LeashPay MCP server (stdio): lets any MCP client (Claude Desktop, ...) shop with a LeashPay allowance.

The AI agent is the brain; LeashPay is the leash. This process holds no money logic: it forwards each
tool call to the LeashPay Agent REST API, where the Policy Judge decides every cent.

Env:  LEASHPAY_API_URL   e.g. https://leashpay-api.onrender.com
      LEASHPAY_AGENT_KEY the lpk_... key created in the LeashPay wallet
Run:  python mcp_server/server.py
"""

import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_text import (  # noqa: E402
    BUY_DESC, CHECK_DESC, PURCHASES_DESC, REFUND_DESC, SEARCH_DESC, WALLET_DESC,
)

TIMEOUT_S = 90.0  # a free-tier Render instance can need ~30 s to wake up

mcp = FastMCP("LeashPay")


def _client() -> httpx.Client:
    url = os.environ.get("LEASHPAY_API_URL", "").rstrip("/")
    key = os.environ.get("LEASHPAY_AGENT_KEY", "")
    if not url or not key:
        raise RuntimeError("Set LEASHPAY_API_URL and LEASHPAY_AGENT_KEY in the MCP server's env.")
    return httpx.Client(base_url=url, headers={"X-Agent-Key": key}, timeout=TIMEOUT_S)


def _call(method: str, path: str, **kw: Any) -> str:
    """One REST call -> the short text the API prepared for the LLM (or a clear error line)."""
    try:
        with _client() as c:
            r = c.request(method, path, **kw)
    except RuntimeError as e:
        return f"ERROR: {e}"
    except httpx.HTTPError as e:
        return f"ERROR: could not reach LeashPay ({type(e).__name__}). The server may be waking up; try again in 30 seconds."
    try:
        body = r.json()
    except ValueError:
        return f"ERROR: LeashPay answered HTTP {r.status_code}"
    if r.is_success:
        return str(body.get("text", body))
    detail = body.get("detail", body)
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(str(d.get("msg", d)) for d in detail)
    return f"ERROR ({r.status_code}): {detail}"


@mcp.tool(name="leashpay_wallet", description=WALLET_DESC)
def leashpay_wallet() -> str:
    return _call("GET", "/agent-api/wallet")


@mcp.tool(name="leashpay_search_store", description=SEARCH_DESC)
def leashpay_search_store(query: str) -> str:
    return _call("GET", "/agent-api/products", params={"q": query})


@mcp.tool(name="leashpay_buy", description=BUY_DESC)
def leashpay_buy(items: list[dict[str, int]], note: str = "") -> str:
    return _call("POST", "/agent-api/buy", json={"items": items, "note": note})


@mcp.tool(name="leashpay_purchases", description=PURCHASES_DESC)
def leashpay_purchases() -> str:
    return _call("GET", "/agent-api/purchases")


@mcp.tool(name="leashpay_check_purchase", description=CHECK_DESC)
def leashpay_check_purchase(purchase_id: int) -> str:
    return _call("GET", f"/agent-api/purchases/{purchase_id}")


@mcp.tool(name="leashpay_refund", description=REFUND_DESC)
def leashpay_refund(purchase_id: int, product_id: Optional[int] = None) -> str:
    return _call("POST", "/agent-api/refund", json={"purchase_id": purchase_id, "product_id": product_id})


if __name__ == "__main__":
    mcp.run()  # stdio
