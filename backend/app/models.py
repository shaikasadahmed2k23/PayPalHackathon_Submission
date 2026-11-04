from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AllowanceStatus(str, Enum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class VoucherStatus(str, Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    REJECTED = "REJECTED"


class PurchaseStatus(str, Enum):
    CAPTURED = "CAPTURED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    BLOCKED = "BLOCKED"
    REFUNDED = "REFUNDED"


class Actor(str, Enum):
    USER = "user"
    SHOPPING_AGENT = "shopping_agent"
    POLICY_JUDGE = "policy_judge"
    SETTLEMENT_AGENT = "settlement_agent"
    MERCHANT = "merchant"


class Policy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    raw_text: str
    policy_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class Allowance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    policy_id: int = Field(foreign_key="policy.id")
    amount: float
    captured_total: float = 0.0
    paypal_order_id: Optional[str] = Field(default=None, index=True)
    authorization_id: Optional[str] = Field(default=None, index=True)
    status: AllowanceStatus = AllowanceStatus.CREATED
    device_pubkey: Optional[str] = None
    certificate: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    expires_at: Optional[datetime] = None


class Merchant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    category: str
    paypal_email: str


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    price: float
    category: str
    merchant_id: int = Field(foreign_key="merchant.id")


class Voucher(SQLModel, table=True):
    # One voucher per (allowance, serial): the DB-level offline double-spend guard.
    __table_args__ = (UniqueConstraint("allowance_id", "serial"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    allowance_id: int = Field(foreign_key="allowance.id", index=True)
    serial: int
    merchant_id: int = Field(foreign_key="merchant.id")
    amount: float
    signature: str
    issued_at: datetime
    scanned_at: Optional[datetime] = None
    status: VoucherStatus = VoucherStatus.PENDING
    capture_id: Optional[str] = None
    payout_batch_id: Optional[str] = None
    judge_reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class Purchase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    allowance_id: int = Field(foreign_key="allowance.id", index=True)
    items_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    amount: float
    status: PurchaseStatus
    capture_id: Optional[str] = None
    judge_reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    actor: Actor
    action: str
    verdict: Optional[str] = None
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    paypal_ref: Optional[str] = None
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
