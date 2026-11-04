import asyncio

from sqlmodel import Session, SQLModel, create_engine

from app.paypal import PayPalError
from app.models import Allowance, AllowanceStatus, Policy as PolicyRow


class FakePayPal:
    def __init__(self) -> None:
        self.captures: list[dict] = []
        self.voided: list[str] = []
        self.payouts: list[dict] = []
        self.refunds: list[dict] = []
        self.refund_error: PayPalError | None = None
        self.payout_error: PayPalError | None = None
        self.capture_error: PayPalError | None = None

    async def create_allowance_order(self, amount, return_url, cancel_url):
        return "ORDER1", "https://paypal.test/approve"

    async def authorize_order(self, order_id):
        return "AUTH1"

    async def capture(self, authorization_id, amount, final, request_id):
        if self.capture_error:
            raise self.capture_error
        await asyncio.sleep(0.02)  # force interleaving between concurrent spends
        self.captures.append(dict(auth=authorization_id, amount=amount, final=final, request_id=request_id))
        return f"CAP{len(self.captures)}", "COMPLETED"

    async def refund(self, capture_id, amount, request_id):
        if self.refund_error:
            raise self.refund_error
        self.refunds.append(dict(capture=capture_id, amount=amount, request_id=request_id))
        return f"REF{len(self.refunds)}", "COMPLETED"

    async def payout(self, receiver_email, amount, sender_batch_id, note):
        if self.payout_error:
            raise self.payout_error
        self.payouts.append(dict(to=receiver_email, amount=amount, batch=sender_batch_id))
        return f"PAYOUT-{sender_batch_id}", "PENDING"

    async def void(self, authorization_id):
        self.voided.append(authorization_id)


def make_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def seed_allowance(engine, amount=20.0, policy=None, **allowance_kw) -> int:
    policy = policy or {"per_tx_max": 10, "daily_max": 60, "require_approval_above": 6}
    with Session(engine) as s:
        p = PolicyRow(raw_text="t", policy_json=policy)
        s.add(p)
        s.commit()
        a = Allowance(policy_id=p.id, amount=amount, status=AllowanceStatus.ACTIVE,
                      authorization_id="AUTH1", paypal_order_id="ORDER1", **allowance_kw)
        s.add(a)
        s.commit()
        return a.id


