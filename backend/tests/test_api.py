import base64
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey
from sqlmodel import Session

from app import crypto
from app.db import get_session
from app.main import app
from app.routers import allowance as allowance_router
from app.services import spend as spend_mod
from tests.fakes import FakePayPal, make_engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine, pp = make_engine(tmp_path), FakePayPal()
    monkeypatch.setattr(spend_mod, "get_paypal_client", lambda: pp)
    monkeypatch.setattr(allowance_router, "get_paypal_client", lambda: pp)
    spend_mod._locks.clear()

    def sess():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = sess
    pp.engine = engine  # lets tests seed rows directly
    yield TestClient(app), pp
    app.dependency_overrides.clear()


def body(amount, category):
    return {"amount": amount, "category": category, "merchant_name": "Cafe"}


def test_full_lifecycle(client):
    c, pp = client
    pol = {"allowed_categories": ["food"], "per_tx_max": 10, "require_approval_above": 6}
    pid = c.post("/policy", json={"raw_text": "t", "policy": pol}).json()["id"]
    r = c.post("/allowance", json={"policy_id": pid, "amount": 20}).json()
    aid = r["allowance_id"]
    assert r["approve_url"].startswith("https://paypal.test")

    pub = base64.b64encode(bytes(SigningKey.generate().verify_key)).decode()
    auth = c.post(f"/allowance/{aid}/authorize", json={"device_pubkey_b64": pub}).json()
    cert = auth["certificate"]
    assert cert["max_vouchers"] == 4 and cert["per_tap_max"] == 5 and cert["device_pubkey"] == pub
    assert cert["allowed_categories"] == ["food"] and cert["blocked_categories"] == []
    assert crypto.verify_server_sig(cert, auth["signature"])
    assert auth["server_pubkey"] == c.get("/crypto/server-pubkey").json()["pubkey_b64"]

    spend = lambda amt, cat: c.post(f"/allowance/{aid}/test-spend", json=body(amt, cat)).json()
    assert spend(3, "food")["verdict"]["decision"] == "PASS"
    r8 = spend(8, "food")
    assert r8["verdict"]["decision"] == "NEEDS_APPROVAL"
    assert c.get("/approvals?status=PENDING").json()[0]["id"] == r8["approval_id"]
    assert c.post(f"/approvals/{r8['approval_id']}/approve").json()["capture_id"]
    assert c.post(f"/approvals/{r8['approval_id']}/approve").status_code == 409
    assert spend(4, "alcohol")["verdict"]["decision"] == "FAIL"

    info = c.get(f"/allowance/{aid}").json()
    assert info["captured_total"] == 11 and info["remaining"] == 9 and info["spent_today"] == 11
    assert info["certificate"] == cert

    assert c.post(f"/allowance/{aid}/close?force=true").json()["status"] == "CLOSED"
    assert pp.voided == ["AUTH1"]
    assert spend(1, "food")["verdict"]["decision"] == "FAIL"


def test_deny_and_bad_pubkey(client):
    c, _ = client
    pid = c.post("/policy", json={"raw_text": "t", "policy": {"require_approval_above": 1}}).json()["id"]
    aid = c.post("/allowance", json={"policy_id": pid, "amount": 20}).json()["allowance_id"]
    assert c.post(f"/allowance/{aid}/authorize", json={"device_pubkey_b64": "AAAA"}).status_code == 422
    c.post(f"/allowance/{aid}/authorize", json={"device_pubkey_b64": base64.b64encode(bytes(32)).decode()})
    ap = c.post(f"/allowance/{aid}/test-spend", json=body(5, "food")).json()
    d = c.post(f"/approvals/{ap['approval_id']}/deny")
    assert d.json()["status"] == "DENIED"
    assert c.post(f"/approvals/{ap['approval_id']}/approve").status_code == 409


def _active_allowance(c, per_tap=5):
    pid = c.post("/policy", json={"raw_text": "t", "policy": {"offline": {"per_tap_max": per_tap}}}).json()["id"]
    aid = c.post("/allowance", json={"policy_id": pid, "amount": 20}).json()["allowance_id"]
    pub = base64.b64encode(bytes(32)).decode()
    c.post(f"/allowance/{aid}/authorize", json={"device_pubkey_b64": pub})
    return aid


def test_close_needs_force_while_vouchers_may_be_unsynced(client):
    c, pp = client
    aid = _active_allowance(c)
    st = c.get(f"/allowance/{aid}/offline-status").json()
    assert st == {"max_vouchers": 4, "settled": 0, "rejected": 0, "highest_issued": None,
                  "exact": False, "unknown_serials": [0, 1, 2, 3]}
    r = c.post(f"/allowance/{aid}/close")
    assert r.status_code == 409
    assert "4 offline vouchers not yet synced" in r.json()["detail"]["message"]
    assert pp.voided == [] and c.get(f"/allowance/{aid}").json()["status"] == "ACTIVE"
    assert c.post(f"/allowance/{aid}/close?force=true").status_code == 200
    assert pp.voided == ["AUTH1"]


def test_close_without_force_when_every_serial_is_accounted_for(client):
    from app.models import Voucher, VoucherStatus
    c, pp = client
    aid = _active_allowance(c)
    with Session(pp.engine) as s:
        for serial, status in enumerate([VoucherStatus.SETTLED, VoucherStatus.SETTLED_PAYOUT_PENDING,
                                         VoucherStatus.REJECTED, VoucherStatus.SETTLED]):
            s.add(Voucher(allowance_id=aid, serial=serial, merchant_id=1, amount=1, signature="s",
                          issued_at=datetime.now(timezone.utc), status=status))
        s.commit()
    st = c.get(f"/allowance/{aid}/offline-status").json()
    assert st["settled"] == 3 and st["rejected"] == 1 and st["unknown_serials"] == []
    assert c.post(f"/allowance/{aid}/close").status_code == 200


def test_close_without_force_when_offline_disabled(client):
    c, _ = client
    pid = c.post("/policy", json={"raw_text": "t", "policy": {"offline": {"enabled": False}}}).json()["id"]
    aid = c.post("/allowance", json={"policy_id": pid, "amount": 20}).json()["allowance_id"]
    c.post(f"/allowance/{aid}/authorize", json={"device_pubkey_b64": base64.b64encode(bytes(32)).decode()})
    assert c.get(f"/allowance/{aid}/offline-status").json()["max_vouchers"] == 0
    assert c.post(f"/allowance/{aid}/close").status_code == 200


def test_wallet_reported_serial_makes_unsynced_count_exact(client):
    from app.models import Voucher, VoucherStatus
    c, pp = client
    aid = _active_allowance(c)  # 4 possible serials
    st = c.post(f"/allowance/{aid}/issued", json={"highest_serial": 1}).json()
    assert st["exact"] and st["unknown_serials"] == [0, 1] and st["highest_issued"] == 1
    assert c.post(f"/allowance/{aid}/issued", json={"highest_serial": 0}).json()["highest_issued"] == 1  # never goes down
    with Session(pp.engine) as s:
        s.add(Voucher(allowance_id=aid, serial=0, merchant_id=1, amount=1, signature="s",
                      issued_at=datetime.now(timezone.utc), status=VoucherStatus.SETTLED))
        s.commit()
    assert c.get(f"/allowance/{aid}/offline-status").json()["unknown_serials"] == [1]
    r = c.post(f"/allowance/{aid}/close")
    assert r.status_code == 409 and r.json()["detail"]["message"].startswith("1 offline vouchers")
    assert c.post(f"/allowance/{aid}/issued", json={"highest_serial": -1}).status_code == 200
    assert c.post(f"/allowance/{aid}/issued", json={"highest_serial": -2}).status_code == 422


def test_wallet_that_never_showed_a_code_can_close_cleanly(client):
    c, _ = client
    aid = _active_allowance(c)
    c.post(f"/allowance/{aid}/issued", json={"highest_serial": -1})
    assert c.get(f"/allowance/{aid}/offline-status").json()["unknown_serials"] == []
    assert c.post(f"/allowance/{aid}/close").status_code == 200


def test_activity_feed_merges_sources_newest_first(client):
    from app.models import Approval, AuditLog, Purchase, PurchaseStatus, Voucher, VoucherStatus
    c, pp = client
    aid = _active_allowance(c)
    t = lambda m: datetime(2026, 11, 5, 12, m, tzinfo=timezone.utc)  # noqa: E731
    line = {"product_id": 1, "name": "Chips", "qty": 2, "merchant": "Taco Truck"}
    with Session(pp.engine) as s:
        s.add(Purchase(allowance_id=aid, items_json=[line], amount=5, status=PurchaseStatus.REFUNDED, created_at=t(1)))
        s.add(Purchase(allowance_id=aid, items_json=[line], amount=5, status=PurchaseStatus.PENDING_APPROVAL, created_at=t(2)))
        s.add(Voucher(allowance_id=aid, serial=0, merchant_id=1, amount=3, signature="s", issued_at=t(3),
                      scanned_at=t(4), status=VoucherStatus.SETTLED))
        s.add(AuditLog(ts=t(5), actor="shopping_agent", action="refund",
                       payload_json={"purchase_id": 1, "amount": 5, "items": ["Chips"]}))
        s.add(Approval(allowance_id=aid, amount=15, category="food", merchant_name="Taco Truck", channel="online",
                       source="purchase", source_id="2", created_at=t(6)))
        s.add(Approval(allowance_id=aid, amount=9, category="food", merchant_name="Old", channel="online",
                       source="manual", status="APPROVED", created_at=t(7)))  # approved: shown via its spend, not here
        s.commit()
    feed = c.get(f"/allowance/{aid}/activity").json()
    assert [(i["kind"], i["status"]) for i in feed] == [
        ("approval", "PENDING"), ("refund", "REFUNDED"), ("voucher", "SETTLED"), ("purchase", "REFUNDED")]
    assert feed[3]["detail"] == "2x Chips" and feed[3]["title"] == "Taco Truck"
    assert c.get("/allowance/999/activity").status_code == 404


def test_audit_ledger_summary_and_allowance_list(client):
    c, pp = client
    aid = _active_allowance(c)
    c.post(f"/allowance/{aid}/test-spend", json={"amount": 3, "category": "food", "merchant_name": "Cafe"})
    c.post(f"/allowance/{aid}/test-spend", json={"amount": 500, "category": "food", "merchant_name": "Bar"})
    audit = c.get(f"/audit?allowance_id={aid}").json()
    assert audit and audit[0]["id"] > audit[-1]["id"]  # newest first
    assert {a["actor"] for a in audit} >= {"policy_judge", "system"}
    assert all(a["verdict"] == "FAIL" for a in c.get(f"/audit?allowance_id={aid}&verdict=FAIL").json())
    assert all(a["actor"] == "system" for a in c.get(f"/audit?allowance_id={aid}&actor=system").json())
    assert c.get("/audit?allowance_id=999").json() == [] and len(c.get(f"/audit?limit=1").json()) == 1
    assert [e["amount"] for e in c.get(f"/ledger?allowance_id={aid}").json()] == [3]
    s = c.get(f"/allowance/{aid}/summary").json()
    assert (s["held"], s["captured"], s["refunded"], s["returned_on_close"]) == (20, 3, 0, 0)
    c.post(f"/allowance/{aid}/close?force=true")
    assert c.get(f"/allowance/{aid}/summary").json()["returned_on_close"] == 17
    assert c.get("/allowance").json()[0] == {"allowance_id": aid, "amount": 20.0, "status": "CLOSED", "captured_total": 3.0}
