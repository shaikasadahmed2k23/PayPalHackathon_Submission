"""Tests for the stdio server with the REST layer mocked.  Run:  python -m pytest mcp_server -q"""

import asyncio
import json

import httpx
import pytest

import server


def mock_api(monkeypatch, handler):
    monkeypatch.setenv("LEASHPAY_API_URL", "https://api.test")
    monkeypatch.setenv("LEASHPAY_AGENT_KEY", "lpk_test")
    real = httpx.Client
    monkeypatch.setattr(server.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_wallet_sends_the_key_and_returns_the_api_text(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"], seen["url"] = req.headers["x-agent-key"], str(req.url)
        return httpx.Response(200, json={"text": "Allowance remaining: $30.00"})

    mock_api(monkeypatch, handler)
    assert server.leashpay_wallet() == "Allowance remaining: $30.00"
    assert seen == {"key": "lpk_test", "url": "https://api.test/agent-api/wallet"}


def test_buy_posts_items_and_note(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"text": "FAIL: blocked"})

    mock_api(monkeypatch, handler)
    out = server.leashpay_buy([{"product_id": 3, "qty": 2}], "snacks")
    assert out == "FAIL: blocked" and seen["body"] == {"items": [{"product_id": 3, "qty": 2}], "note": "snacks"}


def test_search_check_refund_purchases_routes(monkeypatch):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.url.params.get("q")))
        return httpx.Response(200, json={"text": "ok"})

    mock_api(monkeypatch, handler)
    server.leashpay_search_store("chips")
    server.leashpay_check_purchase(7)
    server.leashpay_refund(7, 2)
    server.leashpay_purchases()
    assert calls == [("GET", "/agent-api/products", "chips"), ("GET", "/agent-api/purchases/7", None),
                     ("POST", "/agent-api/refund", None), ("GET", "/agent-api/purchases", None)]


def test_api_errors_become_clear_text(monkeypatch):
    mock_api(monkeypatch, lambda req: httpx.Response(401, json={"detail": "The agent key for Bot was revoked by the user."}))
    assert server.leashpay_wallet() == "ERROR (401): The agent key for Bot was revoked by the user."


def test_unreachable_server_and_missing_env(monkeypatch):
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    mock_api(monkeypatch, boom)
    assert "waking up" in server.leashpay_wallet()
    monkeypatch.delenv("LEASHPAY_AGENT_KEY")
    assert "LEASHPAY_AGENT_KEY" in server.leashpay_wallet()


def test_six_tools_are_registered_with_descriptions():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "leashpay_wallet", "leashpay_search_store", "leashpay_buy",
        "leashpay_purchases", "leashpay_check_purchase", "leashpay_refund"}
    buy = next(t for t in tools if t.name == "leashpay_buy")
    assert "Never retry" in buy.description or "never retry" in buy.description
    assert "NEEDS_APPROVAL" in buy.description
