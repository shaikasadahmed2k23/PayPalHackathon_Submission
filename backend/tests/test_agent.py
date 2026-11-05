import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agents import shopping_agent as agent
from app.agents.shopping_agent import Turn
from app.db import get_session
from app.llm import LLMError
from app.main import app
from app.models import Allowance, AuditLog, LedgerEntry, Merchant, Product, Purchase
from app.seed import seed_if_empty
from app.services import spend as spend_mod
from tests.fakes import FakePayPal, make_engine, seed_allowance

POLICY = {"allowed_categories": ["food", "transport"], "blocked_categories": ["alcohol"],
          "per_tx_max": 15, "daily_max": 60, "require_approval_above": 8}


class FakeLLM:
    """Stands in for generate_json. Tests set .intent / .plan / .pick; they are merged into the
    single Turn the real agent asks for (extra keys such as a bogus `price` are ignored by pydantic)."""

    def __init__(self):
        self.intent = {"intent": "buy"}
        self.plan = {"items": []}
        self.pick = {"refs": []}
        self.calls = []

    def __call__(self, system, user, schema):
        self.calls.append((schema.__name__, user))
        if schema is not Turn:
            raise LLMError("no llm in tests")
        return Turn.model_validate({
            **self.intent, "items": self.plan.get("items", []), "refund_refs": self.pick.get("refs", []),
        })


@pytest.fixture
def env(tmp_path, monkeypatch):
    pp, llm = FakePayPal(), FakeLLM()
    for mod in (spend_mod, agent):
        monkeypatch.setattr(mod, "get_paypal_client", lambda: pp)
    monkeypatch.setattr(agent, "generate_json", llm)
    spend_mod._locks.clear()
    agent.reset_conversations()
    engine = make_engine(tmp_path)
    with Session(engine) as s:
        seed_if_empty(s, "m@test.com")
    aid = seed_allowance(engine, amount=30, policy=POLICY)
    pp.engine = engine
    return engine, pp, llm, aid


def pid(engine, name):
    with Session(engine) as s:
        return s.exec(select(Product).where(Product.name == name)).one().id


def chat(engine, aid, message="hi", cid=None):
    async def go():
        with Session(engine) as s:
            return await agent.handle(s, aid, message, cid)
    return asyncio.run(go())


def buy(engine, llm, aid, names_qty):
    llm.intent = {"intent": "buy", "query": "x"}
    llm.plan = {"items": [{"product_id": pid(engine, n), "qty": q} for n, q in names_qty]}
    return chat(engine, aid, "buy stuff")


# ---- buying -----------------------------------------------------------------------------

def test_buy_within_rules_is_captured(env):
    engine, pp, llm, aid = env
    r = buy(engine, llm, aid, [("Potato Chips", 1), ("Cookies", 1)])  # both Chai Corner: $6.00
    assert r.intent == "buy" and len(r.purchases) == 1
    p = r.purchases[0]
    assert p.status == "CAPTURED" and p.amount == 6.0 and p.merchant == "Chai Corner" and p.capture_id
    assert pp.captures == [dict(auth="AUTH1", amount=6.0, final=False, request_id=f"purchase-AUTH1-{p.id}")]
    assert "$6.00" in r.reply and "Total charged now: $6.00" in r.reply
    with Session(engine) as s:
        entry = s.exec(select(LedgerEntry)).one()
        assert (entry.source, entry.source_id, entry.channel) == ("purchase", str(p.id), "online")
        assert s.get(Allowance, aid).captured_total == 6.0
        assert any(a.actor.value == "shopping_agent" for a in s.exec(select(AuditLog)).all())


def test_mixed_cart_skips_alcohol_and_buys_food(env):
    engine, pp, llm, aid = env
    r = buy(engine, llm, aid, [("Potato Chips", 1), ("IPA Pint", 1)])
    by_merchant = {p.merchant: p for p in r.purchases}
    assert by_merchant["Chai Corner"].status == "CAPTURED"
    assert by_merchant["Craft Beer Tent"].status == "BLOCKED"
    assert r.skipped[0].reasons == ["Alcohol is blocked by your rules"]
    assert "I skipped Craft Beer Tent" in r.reply and "Alcohol is blocked by your rules" in r.reply
    assert len(pp.captures) == 1 and pp.captures[0]["amount"] == 2.5
    with Session(engine) as s:  # the skipped group never reached spend(): no judge entry for it
        verdicts = [a.verdict for a in s.exec(select(AuditLog).where(AuditLog.action == "judge_spend")).all()]
        assert verdicts == ["PASS"]
        skip = s.exec(select(AuditLog).where(AuditLog.action == "skip_group")).one()
        assert skip.actor.value == "shopping_agent" and skip.verdict == "FAIL"


def test_groups_are_split_by_merchant(env):
    engine, pp, llm, aid = env
    r = buy(engine, llm, aid, [("Potato Chips", 1), ("Popcorn", 1)])
    assert sorted(p.merchant for p in r.purchases) == ["Chai Corner", "Taco Truck"]
    assert len(pp.captures) == 2


def test_budget_is_enforced_in_code(env):
    engine, pp, llm, aid = env
    llm.intent = {"intent": "buy", "budget": 5}
    llm.plan = {"items": [{"product_id": pid(engine, "Potato Chips"), "qty": 1},
                          {"product_id": pid(engine, "Popcorn"), "qty": 1}]}  # 2.50 + 3.00 > 5
    r = chat(engine, aid)
    assert [p.merchant for p in r.purchases] == ["Chai Corner"]
    assert r.skipped[0].item == "Popcorn" and "budget" in r.skipped[0].reasons[0]


def test_budget_trimmed_items_collapse_into_one_line(env):
    engine, pp, llm, aid = env
    llm.intent = {"intent": "buy", "budget": 4}
    llm.plan = {"items": [{"product_id": pid(engine, n), "qty": 1}
                          for n in ("Potato Chips", "Popcorn", "Cookies", "Craft IPA 6-Pack")]}
    r = chat(engine, aid)
    assert "Kept it under $4.00 - left out 3 extras (" in r.reply
    assert r.reply.count("It would put the cart over") == 0 and "I skipped" not in r.reply
    assert len(r.skipped) == 3  # structured detail is still there


def test_hallucinated_product_id_is_ignored(env):
    engine, pp, llm, aid = env
    llm.plan = {"items": [{"product_id": 9999, "qty": 1}, {"product_id": pid(engine, "Cookies"), "qty": 1}]}
    r = chat(engine, aid)
    assert [i["name"] for i in r.purchases[0].items] == ["Cookies"] and pp.captures[0]["amount"] == 3.5

    pp.captures.clear()
    llm.plan = {"items": [{"product_id": 424242, "qty": 1}]}
    r = chat(engine, aid)
    assert r.purchases == [] and pp.captures == []
    with Session(engine) as s:
        assert len(s.exec(select(Purchase)).all()) == 1  # nothing created for the bogus cart


def test_llm_price_is_never_used(env):
    engine, pp, llm, aid = env
    llm.plan = {"items": [{"product_id": pid(engine, "Cookies"), "qty": 2, "price": 0.01, "unit_price": 0.01}],
                "total": 0.02}
    r = chat(engine, aid)
    assert r.purchases[0].amount == 7.0 and pp.captures[0]["amount"] == 7.0  # 2 x 3.50 from the DB
    assert r.purchases[0].items[0]["unit_price"] == 3.5


# ---- approvals --------------------------------------------------------------------------

@pytest.fixture
def api(env):
    engine, pp, llm, aid = env

    def sess():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = sess
    yield TestClient(app), engine, pp, llm, aid
    app.dependency_overrides.clear()


def test_over_threshold_waits_for_approval_then_captures(api):
    c, engine, pp, llm, aid = api
    llm.intent = {"intent": "buy"}
    llm.plan = {"items": [{"product_id": pid(engine, "Nachos"), "qty": 2}]}  # $11 > $8
    r = c.post("/agent/chat", json={"allowance_id": aid, "message": "nachos please"}).json()
    p = r["purchases"][0]
    assert p["status"] == "PENDING_APPROVAL" and p["approval_id"] and p["capture_id"] is None
    assert pp.captures == [] and "asked for your approval" in r["reply"]

    ok = c.post(f"/approvals/{p['approval_id']}/approve").json()
    assert ok["capture_id"]
    after = c.get(f"/purchases?allowance_id={aid}").json()[0]
    assert after["status"] == "CAPTURED" and after["capture_id"] == ok["capture_id"]
    assert pp.captures[0]["amount"] == 11.0 and pp.captures[0]["request_id"].startswith("approval-AUTH1-")


def test_denied_approval_blocks_the_purchase(api):
    c, engine, pp, llm, aid = api
    llm.plan = {"items": [{"product_id": pid(engine, "Nachos"), "qty": 2}]}
    p = c.post("/agent/chat", json={"allowance_id": aid, "message": "nachos"}).json()["purchases"][0]
    c.post(f"/approvals/{p['approval_id']}/deny")
    after = c.get(f"/purchases?allowance_id={aid}").json()[0]
    assert after["status"] == "BLOCKED" and after["reasons"] == ["You denied this purchase"]
    assert pp.captures == []


def test_chat_endpoint_404_and_conversation_id(api):
    c, engine, pp, llm, aid = api
    assert c.post("/agent/chat", json={"allowance_id": 999, "message": "hi"}).status_code == 404
    llm.intent = {"intent": "chitchat"}
    r1 = c.post("/agent/chat", json={"allowance_id": aid, "message": "hi"}).json()
    r2 = c.post("/agent/chat", json={"allowance_id": aid, "message": "again",
                                     "conversation_id": r1["conversation_id"]}).json()
    assert r2["conversation_id"] == r1["conversation_id"] and r2["intent"] == "chitchat"


# ---- refunds ----------------------------------------------------------------------------

def refund_chat(engine, llm, aid, refs):
    llm.intent = {"intent": "refund", "item_refs": ["x"]}
    llm.pick = {"refs": refs}
    return chat(engine, aid, "return it")


def test_full_refund(env):
    engine, pp, llm, aid = env
    p = buy(engine, llm, aid, [("Potato Chips", 1), ("Cookies", 1)]).purchases[0]
    r = refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": 0}])
    assert r.refunds[0].amount == 6.0 and r.refunds[0].status == "COMPLETED"
    chips, cookies = pid(engine, "Potato Chips"), pid(engine, "Cookies")
    assert pp.refunds == [dict(capture=p.capture_id, amount=6.0,
                               request_id=f"refund-AUTH1-{p.id}-{min(chips, cookies)}+{max(chips, cookies)}")]
    with Session(engine) as s:
        assert s.get(Purchase, p.id).status.value == "REFUNDED"
        assert s.exec(select(LedgerEntry)).one().refunded_amount == 6.0
        log = s.exec(select(AuditLog).where(AuditLog.action == "refund")).one()
        assert log.actor.value == "shopping_agent" and log.paypal_ref == "REF1"
    assert "Refunded $6.00" in r.reply


def test_partial_refund_then_rest_then_nothing_left(env):
    engine, pp, llm, aid = env
    p = buy(engine, llm, aid, [("Potato Chips", 1), ("Cookies", 1)]).purchases[0]
    chips = pid(engine, "Potato Chips")
    r = refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": chips}])
    assert r.refunds[0].amount == 2.5 and r.refunds[0].items == ["Potato Chips"]
    with Session(engine) as s:
        assert s.get(Purchase, p.id).status.value == "PARTIALLY_REFUNDED"
        assert s.exec(select(LedgerEntry)).one().refunded_amount == 2.5

    # Same item again: nothing to refund, no PayPal call.
    again = refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": chips}])
    assert again.refunds == [] and "already refunded" in again.reply and len(pp.refunds) == 1

    rest = refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": 0}])
    assert rest.refunds[0].amount == 3.5 and rest.refunds[0].items == ["Cookies"]
    with Session(engine) as s:
        assert s.get(Purchase, p.id).status.value == "REFUNDED"
        assert s.exec(select(LedgerEntry)).one().refunded_amount == 6.0


def test_refund_ignores_hallucinated_refs(env):
    engine, pp, llm, aid = env
    p = buy(engine, llm, aid, [("Cookies", 1)]).purchases[0]
    r = refund_chat(engine, llm, aid, [{"purchase_id": 777, "product_id": 0},
                                       {"purchase_id": p.id, "product_id": 31337}])
    assert r.refunds == [] and pp.refunds == [] and "not sure which purchase" in r.reply


def test_refund_never_touches_uncaptured_purchases(env):
    engine, pp, llm, aid = env
    llm.plan = {"items": [{"product_id": pid(engine, "Nachos"), "qty": 2}]}
    p = chat(engine, aid).purchases[0]  # PENDING_APPROVAL
    r = refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": 0}])
    assert pp.refunds == [] and "don't have any purchases" in r.reply


def test_paypal_refund_failure_changes_nothing(env):
    from app.paypal import PayPalError
    engine, pp, llm, aid = env
    p = buy(engine, llm, aid, [("Cookies", 1)]).purchases[0]
    pp.refund_error = PayPalError(422, "Refund not allowed", name="UNPROCESSABLE")
    r = refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": 0}])
    assert r.refunds == [] and "couldn't process" in r.reply
    with Session(engine) as s:
        assert s.get(Purchase, p.id).status.value == "CAPTURED"
        assert s.exec(select(LedgerEntry)).one().refunded_amount == 0


# ---- status / chitchat / robustness -------------------------------------------------------

def test_status_is_built_in_code_with_no_extra_llm_call(env):
    engine, pp, llm, aid = env
    buy(engine, llm, aid, [("Cookies", 1)])
    llm.intent = {"intent": "status"}
    llm.calls.clear()
    r = chat(engine, aid, "how much have I spent?")
    assert "spent $3.50 today" in r.reply and "$26.50 left of your $30.00" in r.reply
    assert "Chai Corner $3.50 (captured)" in r.reply
    assert len(llm.calls) == 1  # the one structured call, nothing to phrase


def test_refund_reduces_spent_today(env):
    engine, pp, llm, aid = env
    p = buy(engine, llm, aid, [("Cookies", 1)]).purchases[0]
    refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": 0}])
    llm.intent = {"intent": "status"}
    assert "spent $0.00 today" in chat(engine, aid, "spent?").reply


def _down(monkeypatch):
    def down(system, user, schema):
        raise LLMError("all providers down")

    monkeypatch.setattr(agent, "generate_json", down)


def test_llm_outage_buy_spends_nothing(env, monkeypatch):
    engine, pp, llm, aid = env
    _down(monkeypatch)
    r = chat(engine, aid, "Get me some chips")
    assert r.intent == "buy" and "haven't spent anything" in r.reply and pp.captures == []


def test_llm_outage_status_still_answers_from_the_ledger(env, monkeypatch):
    engine, pp, llm, aid = env
    buy(engine, llm, aid, [("Cookies", 1)])
    _down(monkeypatch)
    r = chat(engine, aid, "How much have I spent?")
    assert r.intent == "status" and "spent $3.50 today" in r.reply


def test_llm_outage_refund_matches_item_by_keyword(env, monkeypatch):
    engine, pp, llm, aid = env
    p = buy(engine, llm, aid, [("Potato Chips", 1), ("Cookies", 1)]).purchases[0]
    _down(monkeypatch)
    r = chat(engine, aid, "Return the cookies")
    assert r.intent == "refund" and r.refunds[0].amount == 3.5 and r.refunds[0].items == ["Cookies"]
    assert pp.refunds[0]["capture"] == p.capture_id
    assert "I'm not sure" in chat(engine, aid, "Refund the unicorn").reply  # no keyword match -> asks


@pytest.mark.parametrize("message,expected", [
    ("How much have I spent?", "status"), ("what's my balance", "status"),
    ("Return the popcorn", "refund"), ("refund that please", "refund"),
    ("Get me snacks", "buy"), ("book a ride", "buy"), ("grab a beer", "buy"), ("order nachos", "buy"),
    ("hello there", "chitchat"),
])
def test_keyword_intent_fallback(message, expected):
    assert agent.keyword_intent(message) == expected


def test_one_llm_call_per_turn_for_every_intent(env):
    engine, pp, llm, aid = env
    p = buy(engine, llm, aid, [("Cookies", 1)]).purchases[0]
    assert len(llm.calls) == 1
    refund_chat(engine, llm, aid, [{"purchase_id": p.id, "product_id": 0}])
    assert len(llm.calls) == 2
    llm.intent = {"intent": "status"}
    chat(engine, aid)
    llm.intent = {"intent": "chitchat", "reply": "Hello! Ask me for snacks."}
    assert chat(engine, aid).reply == "Hello! Ask me for snacks."
    assert len(llm.calls) == 4


def test_beer_request_is_blocked_by_the_judge_not_the_llm(env):
    engine, pp, llm, aid = env
    llm.intent = {"intent": "buy"}
    llm.plan = {"items": [{"product_id": pid(engine, "Craft IPA 6-Pack"), "qty": 1}]}
    r = chat(engine, aid, "Also grab a 6-pack of beer")
    assert r.intent == "buy" and pp.captures == []
    assert r.purchases[0].status == "BLOCKED" and r.purchases[0].merchant == "Craft Beer Tent"
    assert r.purchases[0].reasons == ["Alcohol is blocked by your rules"]
    assert r.skipped[0].reasons == ["Alcohol is blocked by your rules"]
    assert "I skipped Craft Beer Tent" in r.reply and "Alcohol is blocked by your rules" in r.reply
    with Session(engine) as s:
        assert [p.status.value for p in s.exec(select(Purchase)).all()] == ["BLOCKED"]
    # The picking LLM sees the catalog but none of the user's rules.
    prompt = llm.calls[-1][1]
    assert "Craft IPA 6-Pack" in prompt
    for secret in ("blocked_categories", "per_tx_max", "require_approval_above", "allowed_categories"):
        assert secret not in prompt


def test_conversation_history_keeps_last_10_turns(env):
    engine, pp, llm, aid = env
    llm.intent = {"intent": "chitchat"}
    cid = chat(engine, aid, "m0").conversation_id
    for i in range(1, 15):
        chat(engine, aid, f"m{i}", cid)
    turns = agent._history[cid]
    assert len(turns) == 20 and turns[0]["text"] == "m5" and turns[-2]["text"] == "m14"
    last_intent_call = [c for c in llm.calls if c[0] == "Turn"][-1][1]
    assert "m13" in last_intent_call  # recent turns are passed to the LLM


def test_search_prefers_keyword_matches_but_keeps_whole_catalog(env):
    engine, pp, llm, aid = env
    with Session(engine) as s:
        rows = agent.search_products(s, "get me a ride and some beer")
    top = [r["name"] for r in rows if r["keyword_match"]]
    assert set(top) == {"City Ride", "Airport Ride", "IPA Pint", "Craft IPA 6-Pack", "Lager 6-Pack"}
    assert len(rows) == 14 and all(not r["keyword_match"] for r in rows[len(top):])
