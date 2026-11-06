"""External agents: keys, REST /agent-api, the per-key cap, the leash (no approvals), audit and /mcp."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import mcp_http
from app.db import get_session
from app.main import app
from app.models import AgentKey, Allowance, AllowanceStatus, AuditLog, Merchant, Product
from app.seed import seed_if_empty
from app.services import purchase as purchase_mod, spend as spend_mod
from tests.fakes import FakePayPal, make_engine, seed_allowance

POLICY = {"allowed_categories": ["food", "transport"], "blocked_categories": ["alcohol"],
          "per_tx_max": 15, "daily_max": 60, "require_approval_above": 8}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pp = FakePayPal()
    for mod in (spend_mod, purchase_mod):
        monkeypatch.setattr(mod, "get_paypal_client", lambda: pp)
    spend_mod._locks.clear()
    engine = make_engine(tmp_path)
    with Session(engine) as s:
        seed_if_empty(s, "m@test.com")
    aid = seed_allowance(engine, amount=30, policy=POLICY)

    def sess():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = sess
    monkeypatch.setattr(mcp_http, "engine", engine)
    c = TestClient(app)
    yield c, engine, pp, aid
    app.dependency_overrides.clear()


def pid(engine, name):
    with Session(engine) as s:
        return s.exec(select(Product).where(Product.name == name)).one().id


def new_key(c, aid, name="Claude Desktop", cap=None):
    r = c.post(f"/allowance/{aid}/agent-keys", json={"name": name, "per_purchase_cap": cap})
    assert r.status_code == 200, r.text
    return r.json()


def buy(c, key, engine, *names_qty, note="test"):
    return c.post("/agent-api/buy", headers={"X-Agent-Key": key},
                  json={"items": [{"product_id": pid(engine, n), "qty": q} for n, q in names_qty], "note": note})


# ---- keys -----------------------------------------------------------------------------------

def test_key_is_shown_once_and_stored_hashed(env):
    c, engine, pp, aid = env
    made = new_key(c, aid, cap=5)
    key = made["key"]
    assert key.startswith("lpk_") and len(key) == 36 and made["key_prefix"] == key[:8]
    listed = c.get(f"/allowance/{aid}/agent-keys").json()
    assert len(listed) == 1 and "key" not in listed[0] and "key_hash" not in listed[0]
    assert listed[0]["per_purchase_cap"] == 5 and listed[0]["last_used_at"] is None
    with Session(engine) as s:
        row = s.exec(select(AgentKey)).one()
        assert row.key_hash != key and key not in row.key_hash and len(row.key_hash) == 64


def test_key_auth_valid_missing_wrong_revoked(env):
    c, engine, pp, aid = env
    made = new_key(c, aid)
    assert c.get("/agent-api/wallet", headers={"X-Agent-Key": made["key"]}).status_code == 200
    assert c.get("/agent-api/wallet", headers={"Authorization": f"Bearer {made['key']}"}).status_code == 200
    assert c.get("/agent-api/wallet").status_code == 401
    assert c.get("/agent-api/wallet", headers={"X-Agent-Key": "lpk_nope"}).status_code == 401
    assert c.get(f"/allowance/{aid}/agent-keys").json()[0]["last_used_at"] is not None

    assert c.delete(f"/agent-keys/{made['id']}").json()["revoked_at"] is not None
    r = c.get("/agent-api/wallet", headers={"X-Agent-Key": made["key"]})
    assert r.status_code == 401 and "revoked" in r.json()["detail"] and "Claude Desktop" in r.json()["detail"]


def test_closed_allowance_is_refused(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    with Session(engine) as s:
        a = s.get(Allowance, aid)
        a.status = AllowanceStatus.CLOSED
        s.add(a)
        s.commit()
    r = c.get("/agent-api/wallet", headers={"X-Agent-Key": key})
    assert r.status_code == 403 and "closed" in r.json()["detail"]


def test_key_only_sees_its_own_allowance(env):
    c, engine, pp, aid = env
    other = seed_allowance(engine, amount=20, policy=POLICY)
    k1, k2 = new_key(c, aid)["key"], new_key(c, other, name="Other")["key"]
    assert buy(c, k1, engine, ("Potato Chips", 1)).json()["purchases"][0]["verdict"] == "PASS"
    pid1 = c.get("/agent-api/purchases", headers={"X-Agent-Key": k1}).json()["purchases"][0]["purchase_id"]
    assert c.get(f"/agent-api/purchases/{pid1}", headers={"X-Agent-Key": k2}).status_code == 404
    assert c.get("/agent-api/purchases", headers={"X-Agent-Key": k2}).json()["purchases"] == []
    assert c.post("/agent-api/refund", headers={"X-Agent-Key": k2}, json={"purchase_id": pid1}).status_code == 404


def test_create_key_validates(env):
    c, engine, pp, aid = env
    assert c.post(f"/allowance/{aid}/agent-keys", json={"name": ""}).status_code == 422
    assert c.post(f"/allowance/{aid}/agent-keys", json={"name": "x", "per_purchase_cap": 0}).status_code == 422
    assert c.post("/allowance/999/agent-keys", json={"name": "x"}).status_code == 404


# ---- buying ---------------------------------------------------------------------------------

def test_wallet_lists_rules_and_cap(env):
    c, engine, pp, aid = env
    key = new_key(c, aid, cap=4)["key"]
    w = c.get("/agent-api/wallet", headers={"X-Agent-Key": key}).json()
    assert w["remaining"] == 30 and w["spent_today"] == 0 and w["agent"] == {"name": "Claude Desktop", "per_purchase_cap": 4}
    assert any("Blocked categories: alcohol" in r for r in w["rules"])
    assert any("need the user's approval" in r for r in w["rules"]) and "$4.00" in w["text"]


def test_search_products(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    hits = c.get("/agent-api/products", params={"q": "chips"}, headers={"X-Agent-Key": key}).json()["products"]
    assert [h["name"] for h in hits] == ["Potato Chips"] and hits[0]["price"] == 2.5
    assert len(c.get("/agent-api/products", headers={"X-Agent-Key": key}).json()["products"]) > 5


def test_buy_passes_and_captures(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    r = buy(c, key, engine, ("Potato Chips", 1), ("Cookies", 1)).json()
    p = r["purchases"][0]
    assert p["verdict"] == "PASS" and p["amount"] == 6.0 and r["charged"] == 6.0 and r["remaining"] == 24.0
    assert "PASS: bought" in r["text"] and "$24.00" in r["text"]
    assert len(pp.captures) == 1 and pp.captures[0]["amount"] == 6.0


def test_agent_cannot_buy_alcohol(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    r = buy(c, key, engine, ("IPA Pint", 1)).json()
    assert r["purchases"][0]["verdict"] == "FAIL" and r["charged"] == 0
    assert r["purchases"][0]["reasons"] == ["Alcohol is blocked by your rules"]
    assert "FAIL:" in r["text"] and "Alcohol is blocked" in r["text"] and "Do not retry" in r["text"]
    assert pp.captures == []


def test_per_key_cap_rule(env):
    c, engine, pp, aid = env
    key = new_key(c, aid, cap=5)["key"]
    r = buy(c, key, engine, ("Potato Chips", 1), ("Cookies", 1)).json()  # $6.00 > $5.00 cap
    assert r["purchases"][0]["verdict"] == "FAIL"
    assert r["purchases"][0]["reasons"] == ["Over the $5.00 cap you set for Claude Desktop"]
    assert pp.captures == []
    ok = buy(c, key, engine, ("Potato Chips", 1)).json()  # $2.50 is fine
    assert ok["purchases"][0]["verdict"] == "PASS"


def test_unknown_product_is_reported(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    r = c.post("/agent-api/buy", headers={"X-Agent-Key": key}, json={"items": [{"product_id": 9999}]}).json()
    assert r["purchases"] == [] and "not in the store" in r["skipped"][0]["reasons"][0] and "FAIL" in r["text"]
    assert c.post("/agent-api/buy", headers={"X-Agent-Key": key}, json={"items": []}).status_code == 422


def test_pending_approval_and_the_agent_cannot_approve_itself(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    r = buy(c, key, engine, ("Potato Chips", 2), ("Cookies", 1)).json()  # $8.50-ish > $8 -> needs approval
    p = r["purchases"][0]
    assert p["verdict"] == "NEEDS_APPROVAL" and r["charged"] == 0 and pp.captures == []
    assert "approve it in their LeashPay wallet" in r["text"]
    pending = c.get(f"/agent-api/purchases/{p['purchase_id']}", headers={"X-Agent-Key": key}).json()
    assert pending["verdict"] == "NEEDS_APPROVAL" and pending["text"].startswith("STILL PENDING")

    # the agent key is refused on every non-agent route, whichever way it is presented
    for hdr in ({"X-Agent-Key": key}, {"Authorization": f"Bearer {key}"}):
        assert c.post("/approvals/1/approve", headers=hdr, json={}).status_code == 403
        assert c.post("/approvals/1/deny", headers=hdr, json={}).status_code == 403
        assert c.post("/policy", headers=hdr, json={"raw_text": "x", "policy": {}}).status_code == 403
        assert c.post(f"/allowance/{aid}/close", headers=hdr).status_code == 403
        assert c.post(f"/allowance/{aid}/agent-keys", headers=hdr, json={"name": "me"}).status_code == 403
    assert pp.captures == []

    # the user (no agent key) approves; the agent then sees the purchase captured
    assert c.post("/approvals/1/approve", json={}).status_code == 200
    done = c.get(f"/agent-api/purchases/{p['purchase_id']}", headers={"X-Agent-Key": key}).json()
    assert done["verdict"] == "PASS" and len(pp.captures) == 1


def test_agent_refund(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    p = buy(c, key, engine, ("Potato Chips", 1), ("Cookies", 1)).json()["purchases"][0]
    r = c.post("/agent-api/refund", headers={"X-Agent-Key": key},
               json={"purchase_id": p["purchase_id"], "product_id": pid(engine, "Cookies")}).json()
    assert r["refunded"] is True and r["amount"] == 3.5 and "Refunded $3.50" in r["text"]
    again = c.post("/agent-api/refund", headers={"X-Agent-Key": key},
                   json={"purchase_id": p["purchase_id"], "product_id": pid(engine, "Cookies")}).json()
    assert again["refunded"] is False
    assert c.post("/agent-api/refund", headers={"X-Agent-Key": key},
                  json={"purchase_id": p["purchase_id"], "product_id": 9999}).status_code == 409


def test_audit_rows_name_the_external_agent(env):
    c, engine, pp, aid = env
    key = new_key(c, aid, name="My Bot")
    buy(c, key["key"], engine, ("Potato Chips", 1), note="snack")
    buy(c, key["key"], engine, ("IPA Pint", 1))
    with Session(engine) as s:
        rows = s.exec(select(AuditLog).where(AuditLog.actor == "external_agent")).all()
        actions = {r.action for r in rows}
        assert {"propose_cart", "skip_group"} <= actions
        for r in rows:
            assert r.payload_json["agent"] == "My Bot" and r.payload_json["key_prefix"] == key["key_prefix"]
        judged = s.exec(select(AuditLog).where(AuditLog.action == "judge_spend")).all()
        assert judged and judged[0].payload_json["agent"] == "My Bot"  # the Policy Judge row says who asked
    out = c.get("/audit", params={"actor": "external_agent"}).json()
    assert out and all(row["actor"] == "external_agent" for row in out)


def test_chat_agent_still_logs_as_shopping_agent(env):
    c, engine, pp, aid = env
    import asyncio
    with Session(engine) as s:
        asyncio.run(purchase_mod.buy_items(s, aid, [{"product_id": pid(engine, "Potato Chips"), "qty": 1}]))
        assert {r.actor.value for r in s.exec(select(AuditLog)).all()} >= {"shopping_agent"}


# ---- remote MCP endpoint -----------------------------------------------------------------------

def test_remote_mcp_end_to_end(env, monkeypatch):
    """Initialize + list tools + call tools over Streamable HTTP, authenticated by the bearer key."""
    c, engine, pp, aid = env
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "init_db", lambda: None)  # never touch the real database
    monkeypatch.setattr(main_mod, "engine", engine)
    key = new_key(c, aid, cap=5)["key"]
    hdr = {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"}

    def rpc(method, params=None, h=hdr):
        r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        assert r.status_code == 200, r.text
        return r.json()["result"]

    def call(name, args=None, h=hdr):
        return rpc("tools/call", {"name": name, "arguments": args or {}}, h)["content"][0]["text"]

    with c:  # runs the lifespan (starts the MCP session manager); init_db is patched out below
        names = {t["name"] for t in rpc("tools/list")["tools"]}
        assert names == {"leashpay_wallet", "leashpay_search_store", "leashpay_buy",
                         "leashpay_purchases", "leashpay_check_purchase", "leashpay_refund"}
        assert "Left on hold: $30.00 of $30.00" in call("leashpay_wallet")
        assert "Potato Chips" in call("leashpay_search_store", {"query": "chips"})
        chips = pid(engine, "Potato Chips")
        assert call("leashpay_buy", {"items": [{"product_id": chips, "qty": 1}], "note": "x"}).startswith("PASS: bought")
        cookies = pid(engine, "Cookies")
        over = call("leashpay_buy", {"items": [{"product_id": chips, "qty": 1}, {"product_id": cookies, "qty": 1}]})
        assert over.startswith("FAIL:") and "Over the $5.00 cap you set for Claude Desktop" in over
        assert "#1" in call("leashpay_purchases") and "PASS" in call("leashpay_check_purchase", {"purchase_id": 1})
        assert call("leashpay_refund", {"purchase_id": 1}).startswith("Refunded $2.50")
        bad = call("leashpay_wallet", h={"Authorization": "Bearer lpk_wrong", "Accept": hdr["Accept"]})
        assert bad.startswith("ERROR: Invalid agent key")


def test_stdio_server_descriptions_match_backend():
    """mcp_server/server.py is standalone; its tool descriptions must equal the remote endpoint's."""
    import importlib.util
    from pathlib import Path
    from app import tools_text

    path = Path(__file__).resolve().parents[2] / "mcp_server" / "tool_text.py"
    spec = importlib.util.spec_from_file_location("tool_text", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    for name in ("WALLET_DESC", "SEARCH_DESC", "BUY_DESC", "PURCHASES_DESC", "CHECK_DESC", "REFUND_DESC"):
        assert getattr(mod, name) == getattr(tools_text, name), name


# ---- polish: balance wording, reason order, audit of successful purchases ----------------------

def test_refund_is_gross_spend_and_does_not_refill_the_hold(env):
    c, engine, pp, aid = env
    key = new_key(c, aid)["key"]
    h = {"X-Agent-Key": key}
    p = buy(c, key, engine, ("Potato Chips", 1)).json()["purchases"][0]
    r = c.post("/agent-api/refund", headers=h, json={"purchase_id": p["purchase_id"]}).json()
    assert "Refunded $2.50 to your PayPal balance" in r["text"] and "Your hold stays at $27.50" in r["text"]
    w = c.get("/agent-api/wallet", headers=h).json()
    assert w["remaining"] == 27.5 and w["spent"] == 2.5 and w["spent_today"] == 2.5  # not netted
    assert w["refunded_total"] == 2.5 and w["refunded_today"] == 2.5
    assert ("Left on hold: $27.50 of $30.00. Spent: $2.50 ($2.50 today). Refunded: $2.50 "
            "(returned to your PayPal balance - refunds don't refill the hold).") in w["text"]
    info = c.get(f"/allowance/{aid}").json()
    assert info["spent_today"] == 2.5 and info["refunded_total"] == 2.5 and info["refunded_today"] == 2.5


def test_rule_violations_come_before_the_agent_cap(env):
    c, engine, pp, aid = env
    key = new_key(c, aid, cap=5)["key"]
    r = buy(c, key, engine, ("IPA Pint", 1)).json()  # $6 alcohol, cap $5
    assert r["purchases"][0]["reasons"] == ["Alcohol is blocked by your rules",
                                            "Over the $5.00 cap you set for Claude Desktop"]


def test_successful_agent_purchase_is_audited_as_external_agent(env):
    c, engine, pp, aid = env
    key = new_key(c, aid, name="My Bot")
    buy(c, key["key"], engine, ("Potato Chips", 1))
    with Session(engine) as s:
        rows = {r.action: r for r in s.exec(select(AuditLog).where(AuditLog.actor == "external_agent")).all()}
    for action in ("judge_spend", "capture"):
        assert rows[action].payload_json["agent"] == "My Bot"
        assert rows[action].payload_json["key_prefix"] == key["key_prefix"]


def test_user_approving_an_agent_purchase_keeps_the_agent_name(env):
    c, engine, pp, aid = env
    key = new_key(c, aid, name="My Bot")["key"]
    buy(c, key, engine, ("Potato Chips", 2), ("Cookies", 1))
    assert c.post("/approvals/1/approve", json={}).status_code == 200
    with Session(engine) as s:
        cap = s.exec(select(AuditLog).where(AuditLog.action == "capture")).all()
        assert cap and cap[-1].actor.value == "external_agent" and cap[-1].payload_json["agent"] == "My Bot"
