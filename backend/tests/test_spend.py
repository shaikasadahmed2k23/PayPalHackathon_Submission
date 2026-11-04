import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.models import Allowance, AllowanceStatus, Approval, AuditLog, LedgerEntry
from app.services import spend as spend_mod
from tests.fakes import FakePayPal, make_engine, seed_allowance


@pytest.fixture
def env(tmp_path, monkeypatch):
    pp = FakePayPal()
    monkeypatch.setattr(spend_mod, "get_paypal_client", lambda: pp)
    spend_mod._locks.clear()
    return make_engine(tmp_path), pp


def run_spend(engine, allowance_id, amount, category="food", skip_approval=False):
    async def go():
        with Session(engine) as s:
            return await spend_mod.spend(
                s, allowance_id, amount, category, "Cafe", "online",
                source="manual", source_id="x", request_id=f"r-{amount}",
                skip_approval=skip_approval,
            )
    return asyncio.run(go())


def test_pass_captures_and_records_ledger(env):
    engine, pp = env
    aid = seed_allowance(engine)
    r = run_spend(engine, aid, 5.0)
    assert r.verdict.decision == "PASS" and r.capture_id == "CAP1"
    assert pp.captures == [dict(auth="AUTH1", amount=5.0, final=False, request_id="r-5.0")]
    with Session(engine) as s:
        assert s.get(Allowance, aid).captured_total == 5.0
        entry = s.exec(select(LedgerEntry)).one()
        assert entry.capture_id == "CAP1" and entry.amount == 5.0
        audits = s.exec(select(AuditLog)).all()
        assert [(a.actor.value, a.action) for a in audits] == [
            ("policy_judge", "judge_spend"), ("system", "capture")]
        assert audits[1].paypal_ref == "CAP1"


def test_needs_approval_creates_approval_without_capture(env):
    engine, pp = env
    aid = seed_allowance(engine)
    r = run_spend(engine, aid, 8.0)
    assert r.verdict.decision == "NEEDS_APPROVAL" and r.approval_id and r.capture_id is None
    assert pp.captures == []
    with Session(engine) as s:
        assert s.get(Approval, r.approval_id).status.value == "PENDING"
        assert s.get(Allowance, aid).captured_total == 0
        assert s.exec(select(LedgerEntry)).all() == []


def test_fail_does_nothing_but_audit(env):
    engine, pp = env
    aid = seed_allowance(engine, policy={"blocked_categories": ["alcohol"]})
    r = run_spend(engine, aid, 4.0, category="alcohol")
    assert r.verdict.decision == "FAIL" and r.capture_id is None and r.approval_id is None
    assert pp.captures == []
    with Session(engine) as s:
        assert s.exec(select(Approval)).all() == [] and s.exec(select(LedgerEntry)).all() == []
        assert [a.verdict for a in s.exec(select(AuditLog)).all()] == ["FAIL"]


def test_skip_approval_captures_above_threshold_but_fail_rules_apply(env):
    engine, pp = env
    aid = seed_allowance(engine)
    assert run_spend(engine, aid, 8.0, skip_approval=True).verdict.decision == "PASS"
    assert run_spend(engine, aid, 15.0, skip_approval=True).verdict.decision == "FAIL"  # > per_tx_max
    assert len(pp.captures) == 1


def test_daily_limit_uses_ledger(env):
    engine, _ = env
    aid = seed_allowance(
        engine, amount=100, policy={"per_tx_max": 50, "daily_max": 12, "require_approval_above": 50})
    assert run_spend(engine, aid, 10.0).verdict.decision == "PASS"
    r = run_spend(engine, aid, 5.0)
    assert r.verdict.decision == "FAIL" and "daily limit" in r.verdict.reasons[0]


def test_allowance_remaining_enforced(env):
    engine, _ = env
    aid = seed_allowance(engine, amount=10, policy={"per_tx_max": 50, "require_approval_above": 50})
    assert run_spend(engine, aid, 8.0).verdict.decision == "PASS"
    assert run_spend(engine, aid, 3.0).verdict.decision == "FAIL"


def test_closed_allowance_fails(env):
    engine, pp = env
    aid = seed_allowance(engine)
    with Session(engine) as s:
        a = s.get(Allowance, aid)
        a.status = AllowanceStatus.CLOSED
        s.add(a)
        s.commit()
    r = run_spend(engine, aid, 1.0)
    assert r.verdict.decision == "FAIL" and "closed" in r.verdict.reasons[0]
    assert pp.captures == []


def test_expired_allowance_fails(env):
    engine, pp = env
    aid = seed_allowance(engine, expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    r = run_spend(engine, aid, 1.0)
    assert r.verdict.decision == "FAIL" and "expired" in r.verdict.reasons[0]
    assert pp.captures == []


def test_lock_prevents_overspend_under_concurrency(env):
    engine, pp = env
    aid = seed_allowance(engine, amount=10, policy={"per_tx_max": 10, "require_approval_above": 10})

    async def go():
        async def one(i):
            with Session(engine) as s:  # separate session per request, like the API
                return await spend_mod.spend(
                    s, aid, 6.0, "food", "Cafe", "online",
                    source="manual", source_id=str(i), request_id=f"c-{i}")
        return await asyncio.gather(one(1), one(2))

    results = asyncio.run(go())
    assert sorted(r.verdict.decision for r in results) == ["FAIL", "PASS"]
    assert len(pp.captures) == 1
    with Session(engine) as s:
        assert s.get(Allowance, aid).captured_total == 6.0


# ---- request ids and late offline sync -------------------------------------------------

def _voucher_env(engine, issued_delta_h, expired=True):
    """Expired allowance whose certificate expired 1h ago; one voucher issued `issued_delta_h` from then."""
    from app.models import Voucher
    exp = datetime.now(timezone.utc) - timedelta(hours=1)
    cert = {"certificate": {"expires_at": exp.strftime("%Y-%m-%dT%H:%M:%SZ")}, "signature": "x"}
    aid = seed_allowance(engine, expires_at=exp, certificate=cert)
    with Session(engine) as s:
        v = Voucher(allowance_id=aid, serial=0, merchant_id=1, amount=3.0, signature="s",
                    issued_at=exp + timedelta(hours=issued_delta_h))
        s.add(v)
        s.commit()
        return aid, str(v.id)


def run_voucher_spend(engine, aid, source, source_id):
    async def go():
        with Session(engine) as s:
            return await spend_mod.spend(s, aid, 3.0, "food", "Cafe", "offline",
                                         source=source, source_id=source_id, request_id="rid")
    return asyncio.run(go())


def test_late_voucher_issued_before_expiry_still_settles(env):
    engine, pp = env
    aid, vid = _voucher_env(engine, issued_delta_h=-2)
    r = run_voucher_spend(engine, aid, "voucher", vid)
    assert r.verdict.decision == "PASS" and len(pp.captures) == 1


def test_late_voucher_issued_after_expiry_fails(env):
    engine, pp = env
    aid, vid = _voucher_env(engine, issued_delta_h=+1)
    r = run_voucher_spend(engine, aid, "voucher", vid)
    assert r.verdict.decision == "FAIL" and "expired" in r.verdict.reasons[0]


def test_expiry_stays_strict_for_non_voucher_spends(env):
    engine, pp = env
    aid, vid = _voucher_env(engine, issued_delta_h=-2)
    assert run_voucher_spend(engine, aid, "manual", vid).verdict.decision == "FAIL"
    assert pp.captures == []


def test_late_voucher_on_closed_allowance_fails(env):
    engine, pp = env
    aid, vid = _voucher_env(engine, issued_delta_h=-2)
    with Session(engine) as s:
        a = s.get(Allowance, aid)
        a.status = AllowanceStatus.CLOSED
        s.add(a)
        s.commit()
    r = run_voucher_spend(engine, aid, "voucher", vid)
    assert r.verdict.decision == "FAIL" and "closed" in r.verdict.reasons[0]


def test_request_id_uses_authorization_id_not_local_id(env):
    engine, _ = env
    aid = seed_allowance(engine)
    with Session(engine) as s:
        assert spend_mod.make_request_id(s.get(Allowance, aid), "voucher", 7) == "voucher-AUTH1-7"
