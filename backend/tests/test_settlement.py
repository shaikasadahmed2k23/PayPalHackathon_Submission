import asyncio

import pytest
from sqlmodel import Session, select

from app.agents import settlement_agent as agent
from app.llm import LLMError
from app.models import Allowance, AuditLog, LedgerEntry, Merchant, Voucher
from app.paypal import PayPalError
from app.seed import seed_if_empty
from app.services import spend as spend_mod
from tests.fakes import FakePayPal, make_engine, seed_allowance
from tests.voucher_helpers import make_payment

POLICY = {"allowed_categories": ["food"], "per_tx_max": 20, "require_approval_above": 1,
          "offline": {"enabled": True, "per_tap_max": 5}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pp = FakePayPal()
    monkeypatch.setattr(spend_mod, "get_paypal_client", lambda: pp)
    monkeypatch.setattr(agent, "get_paypal_client", lambda: pp)
    monkeypatch.setattr(agent, "generate_json", lambda s, u, schema: schema(risk="low", findings=[]))
    spend_mod._locks.clear()
    engine = make_engine(tmp_path)
    with Session(engine) as s:
        seed_if_empty(s, "payout@test.com")
    aid = seed_allowance(engine, amount=20, policy=POLICY)
    return engine, pp, aid


def merchant_id(engine, name):
    with Session(engine) as s:
        return s.exec(select(Merchant).where(Merchant.name == name)).one().id


def run_settle(engine, mid, payments):
    async def go():
        with Session(engine) as s:
            return await agent.settle(s, mid, payments)
    return asyncio.run(go())


def payment(aid, mid, serial=0, amount=3.0, **kw):
    from tests.voucher_helpers import make_cert
    cert, sig = make_cert(allowance_id=aid, allowed=("food",))
    return make_payment(merchant_id=mid, serial=serial, amount=amount, cert=cert, cert_sig=sig, **kw)


def test_valid_vouchers_are_captured_and_paid_out(env):
    engine, pp, aid = env
    chai = merchant_id(engine, "Chai Corner")
    report = run_settle(engine, chai, [payment(aid, chai, 0, 3.0), payment(aid, chai, 1, 4.5)])
    assert [i.status for i in report.items] == ["SETTLED", "SETTLED"]
    assert report.settled == 2 and report.rejected == 0 and report.total_settled_amount == 7.5
    assert [c["request_id"] for c in pp.captures] == ["voucher-AUTH1-0", "voucher-AUTH1-1"]
    assert all(c["final"] is False for c in pp.captures)  # offline never needs approval either
    assert pp.payouts[0] == dict(to="payout@test.com", amount=3.0, batch="voucher-AUTH1-0")
    with Session(engine) as s:
        v = s.exec(select(Voucher).where(Voucher.serial == 0)).one()
        assert v.status.value == "SETTLED" and v.capture_id and v.payout_batch_id
        assert s.get(Allowance, aid).captured_total == 7.5
        assert len(s.exec(select(LedgerEntry)).all()) == 2


def test_replay_of_same_serial_is_rejected_and_not_charged_twice(env):
    engine, pp, aid = env
    chai, taco = merchant_id(engine, "Chai Corner"), merchant_id(engine, "Taco Truck")
    run_settle(engine, taco, [payment(aid, taco, 1, 4.5)])
    report = run_settle(engine, chai, [payment(aid, chai, 1, 4.5)])
    assert report.items[0].status == "REJECTED"
    assert report.items[0].reasons == [agent.REPLAY_REASON]
    assert len(pp.captures) == 1 and len(pp.payouts) == 1
    with Session(engine) as s:
        assert len(s.exec(select(Voucher)).all()) == 1


def test_over_cap_rejected_without_capture(env):
    engine, pp, aid = env
    chai = merchant_id(engine, "Chai Corner")
    report = run_settle(engine, chai, [payment(aid, chai, 2, 7.0)])
    assert report.items[0].status == "REJECTED" and "per-tap limit" in report.items[0].reasons[0]
    assert pp.captures == [] and pp.payouts == []
    with Session(engine) as s:
        assert s.exec(select(Voucher)).one().status.value == "REJECTED"  # authentic voucher is consumed


def test_not_allowed_category_rejected(env):
    engine, pp, aid = env
    gadget = merchant_id(engine, "Gadget Stall")
    report = run_settle(engine, gadget, [payment(aid, gadget, 0, 2.0)])
    assert report.items[0].status == "REJECTED" and "allowed categories" in report.items[0].reasons[0]
    assert pp.captures == []


def test_blocked_category_rejected(env):
    engine, pp, aid = env
    beer = merchant_id(engine, "Craft Beer Tent")
    from tests.voucher_helpers import make_cert
    cert, sig = make_cert(allowance_id=aid, allowed=(), blocked=("alcohol",))
    report = run_settle(engine, beer, [make_payment(merchant_id=beer, serial=0, amount=2, cert=cert, cert_sig=sig)])
    assert report.items[0].reasons == ["Alcohol purchases are blocked by the buyer's rules"]
    assert pp.captures == []


def test_unauthentic_voucher_is_rejected_and_not_stored(env):
    engine, pp, aid = env
    from nacl.signing import SigningKey
    chai = merchant_id(engine, "Chai Corner")
    report = run_settle(engine, chai, [payment(aid, chai, 0, 3.0, device=SigningKey.generate())])
    assert report.items[0].status == "REJECTED"
    with Session(engine) as s:
        assert s.exec(select(Voucher)).all() == []  # a forger cannot burn real serials
    assert pp.captures == []


def test_payment_for_another_merchant_is_rejected(env):
    engine, pp, aid = env
    chai, taco = merchant_id(engine, "Chai Corner"), merchant_id(engine, "Taco Truck")
    report = run_settle(engine, chai, [payment(aid, taco, 0, 3.0)])
    assert "different merchant" in report.items[0].reasons[0]
    assert pp.captures == []


def test_spend_engine_rejection_marks_voucher_rejected(env):
    engine, pp, aid = env
    with Session(engine) as s:
        a = s.get(Allowance, aid)
        a.amount = 2.0
        s.add(a)
        s.commit()
    chai = merchant_id(engine, "Chai Corner")
    report = run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    assert report.items[0].status == "REJECTED" and "left in your allowance" in report.items[0].reasons[0]
    assert pp.captures == []


def test_payout_failure_keeps_capture_as_payout_pending(env):
    engine, pp, aid = env
    pp.payout_error = PayPalError(403, "Authorization failed", name="AUTHORIZATION_ERROR")
    chai = merchant_id(engine, "Chai Corner")
    report = run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    item = report.items[0]
    assert item.status == "SETTLED_PAYOUT_PENDING" and item.capture_id and item.payout_batch_id is None
    assert "AUTHORIZATION_ERROR" in item.reasons[0]
    assert report.settled == 1 and report.payout_pending == 1 and report.total_settled_amount == 3.0
    with Session(engine) as s:
        v = s.exec(select(Voucher)).one()
        assert v.status.value == "SETTLED_PAYOUT_PENDING" and v.capture_id
        assert s.get(Allowance, aid).captured_total == 3.0  # money trail kept


def test_ai_review_failure_does_not_break_settlement(env, monkeypatch):
    engine, pp, aid = env

    def boom(*a):
        raise LLMError("no key")

    monkeypatch.setattr(agent, "generate_json", boom)
    chai = merchant_id(engine, "Chai Corner")
    report = run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    assert report.ai_review.risk == "unknown"
    assert report.items[0].status == "SETTLED" and len(pp.payouts) == 1


def test_ai_findings_are_audited_but_do_not_change_decisions(env, monkeypatch):
    engine, pp, aid = env
    monkeypatch.setattr(agent, "generate_json",
                        lambda s, u, schema: schema(risk="high", findings=["Many vouchers in 2 minutes"]))
    chai = merchant_id(engine, "Chai Corner")
    report = run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    assert report.ai_review.risk == "high" and report.items[0].status == "SETTLED"
    with Session(engine) as s:
        a = s.exec(select(AuditLog).where(AuditLog.action == "anomaly_review")).one()
        assert a.actor.value == "settlement_agent" and a.reasons == ["Many vouchers in 2 minutes"]


def test_capture_failure_leaves_voucher_resyncable(env):
    engine, pp, aid = env
    pp.capture_error = PayPalError(500, "boom")
    chai = merchant_id(engine, "Chai Corner")
    p = payment(aid, chai, 0, 3.0)
    assert run_settle(engine, chai, [p]).items[0].status == "REJECTED"
    with Session(engine) as s:
        assert s.exec(select(Voucher)).all() == []
    pp.capture_error = None
    assert run_settle(engine, chai, [p]).items[0].status == "SETTLED"


def test_late_sync_after_allowance_expiry_settles_via_agent(env):
    from datetime import datetime, timedelta, timezone
    from tests.voucher_helpers import make_cert, z
    engine, pp, aid = env
    exp = datetime.now(timezone.utc) - timedelta(hours=1)
    cert, sig = make_cert(allowance_id=aid, allowed=("food",), expires_in_h=-1)
    with Session(engine) as s:
        a = s.get(Allowance, aid)
        a.expires_at = exp
        a.certificate = {"certificate": cert, "signature": sig}
        s.add(a)
        s.commit()
    chai = merchant_id(engine, "Chai Corner")
    p = make_payment(merchant_id=chai, serial=0, amount=3.0, cert=cert, cert_sig=sig,
                     issued_at=z(exp - timedelta(hours=2)))
    report = run_settle(engine, chai, [p])
    assert report.items[0].status == "SETTLED", report.items[0].reasons


def test_missing_merchant_email_fails_before_calling_paypal(env):
    engine, pp, aid = env
    chai = merchant_id(engine, "Chai Corner")
    with Session(engine) as s:
        m = s.get(Merchant, chai)
        m.paypal_email = ""
        s.add(m)
        s.commit()
    item = run_settle(engine, chai, [payment(aid, chai, 0, 3.0)]).items[0]
    assert item.status == "SETTLED_PAYOUT_PENDING" and item.capture_id
    assert item.reasons == ["Payout failed: Merchant has no PayPal email — set MERCHANT_PAYOUT_EMAIL"]
    assert pp.payouts == []


def run_retry(engine, allowance_id=None):
    async def go():
        with Session(engine) as s:
            return await agent.retry_payouts(s, allowance_id)
    return asyncio.run(go())


def test_retry_payouts_reuses_sender_batch_id_and_settles(env):
    engine, pp, aid = env
    pp.payout_error = PayPalError(400, "Invalid request", name="VALIDATION_ERROR")
    chai, taco = merchant_id(engine, "Chai Corner"), merchant_id(engine, "Taco Truck")
    run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    run_settle(engine, taco, [payment(aid, taco, 1, 4.0)])
    assert pp.payouts == []

    still = run_retry(engine, aid)  # PayPal still failing
    assert (still.retried, still.settled, still.still_pending) == (2, 0, 2)

    pp.payout_error = None
    report = run_retry(engine, aid)
    assert (report.retried, report.settled, report.still_pending) == (2, 2, 0)
    assert [p["batch"] for p in pp.payouts] == ["voucher-AUTH1-0", "voucher-AUTH1-1"]  # same ids as first try
    with Session(engine) as s:
        vs = s.exec(select(Voucher).order_by(Voucher.serial)).all()
        assert all(v.status.value == "SETTLED" and v.payout_batch_id and v.judge_reasons == [] for v in vs)
        assert len(pp.captures) == 2  # no second capture
        assert s.get(Allowance, aid).captured_total == 7.0
    assert run_retry(engine, aid).retried == 0  # nothing left to retry
    assert len(pp.payouts) == 2


def test_retry_only_touches_the_given_allowance(env):
    engine, pp, aid = env
    pp.payout_error = PayPalError(400, "x")
    chai = merchant_id(engine, "Chai Corner")
    run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    pp.payout_error = None
    assert run_retry(engine, aid + 99).retried == 0
    assert run_retry(engine, aid).settled == 1


def test_ai_review_failure_reason_is_audited_with_exception_type(env, monkeypatch):
    engine, pp, aid = env

    def boom(*a):
        raise LLMError("gemini call failed: ServerError: 503 overloaded")

    monkeypatch.setattr(agent, "generate_json", boom)
    chai = merchant_id(engine, "Chai Corner")
    run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    with Session(engine) as s:
        a = s.exec(select(AuditLog).where(AuditLog.action == "anomaly_review")).one()
        assert a.verdict == "unknown"
        assert a.reasons == ["AI review unavailable: LLMError: gemini call failed: ServerError: 503 overloaded"]


def test_resending_a_settled_voucher_is_acknowledged_not_rejected(env):
    engine, pp, aid = env
    chai = merchant_id(engine, "Chai Corner")
    run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    again = run_settle(engine, chai, [payment(aid, chai, 0, 3.0), payment(aid, chai, 1, 2.0)])
    first, second = again.items
    assert first.status == "SETTLED" and first.already and first.reasons == [agent.ALREADY_SETTLED]
    assert first.capture_id and first.payout_batch_id
    assert second.status == "SETTLED" and not second.already
    assert (again.settled, again.already_settled, again.rejected) == (1, 1, 0)
    assert again.total_settled_amount == 2.0 and "already been settled" in again.summary
    assert len(pp.captures) == 2 and len(pp.payouts) == 2  # nothing charged or paid twice


def test_resend_with_different_amount_or_merchant_is_still_a_replay(env):
    engine, pp, aid = env
    chai, taco = merchant_id(engine, "Chai Corner"), merchant_id(engine, "Taco Truck")
    run_settle(engine, chai, [payment(aid, chai, 0, 3.0)])
    other_amount = run_settle(engine, chai, [payment(aid, chai, 0, 4.0)])
    other_merchant = run_settle(engine, taco, [payment(aid, taco, 0, 3.0)])
    for r in (other_amount, other_merchant):
        assert r.items[0].status == "REJECTED" and r.items[0].reasons == [agent.REPLAY_REASON]
        assert r.already_settled == 0 and r.rejected == 1
    assert len(pp.captures) == 1


def test_resend_of_a_rejected_voucher_is_not_treated_as_settled(env):
    engine, pp, aid = env
    chai = merchant_id(engine, "Chai Corner")
    run_settle(engine, chai, [payment(aid, chai, 0, 9.0)])  # over the per-tap cap -> rejected
    again = run_settle(engine, chai, [payment(aid, chai, 0, 9.0)])
    assert again.items[0].status == "REJECTED" and again.already_settled == 0
