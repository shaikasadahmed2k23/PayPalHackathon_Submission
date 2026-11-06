from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


# Timezone-aware columns: Postgres stores timestamptz; SQLite ignores the flag (code already treats naive as UTC).
TZ = DateTime(timezone=True)


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
    SETTLED_PAYOUT_PENDING = "SETTLED_PAYOUT_PENDING"  # captured, merchant payout failed
    REJECTED = "REJECTED"


class PurchaseStatus(str, Enum):
    CAPTURED = "CAPTURED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    BLOCKED = "BLOCKED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class Actor(str, Enum):
    USER = "user"
    SYSTEM = "system"
    SHOPPING_AGENT = "shopping_agent"
    POLICY_JUDGE = "policy_judge"
    SETTLEMENT_AGENT = "settlement_agent"
    MERCHANT = "merchant"
    EXTERNAL_AGENT = "external_agent"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class Policy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    raw_text: str
    policy_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_type=TZ)


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
    expires_at: Optional[datetime] = Field(default=None, sa_type=TZ)


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
    issued_at: datetime = Field(sa_type=TZ)
    scanned_at: Optional[datetime] = Field(default=None, sa_type=TZ)
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
    created_at: Optional[datetime] = Field(default_factory=utcnow, sa_type=TZ)  # NULL on rows from older DBs


class IssuedMark(SQLModel, table=True):
    """Highest voucher serial the buyer's wallet says it has shown (-1 = none yet)."""
    allowance_id: int = Field(foreign_key="allowance.id", primary_key=True)
    highest_serial: int = -1


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True, sa_type=TZ)
    actor: Actor
    action: str
    verdict: Optional[str] = None
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    paypal_ref: Optional[str] = None
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class LedgerEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    allowance_id: int = Field(foreign_key="allowance.id", index=True)
    amount: float
    category: str
    merchant_name: str
    channel: str  # online | offline
    source: str  # purchase | voucher | manual
    source_id: Optional[str] = None
    capture_id: Optional[str] = None
    refunded_amount: float = 0.0
    created_at: datetime = Field(default_factory=utcnow, sa_type=TZ)


class Approval(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    allowance_id: int = Field(foreign_key="allowance.id", index=True)
    amount: float
    category: str
    merchant_name: str
    channel: str
    source: str
    source_id: Optional[str] = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow, sa_type=TZ)
    decided_at: Optional[datetime] = Field(default=None, sa_type=TZ)


class AgentKey(SQLModel, table=True):
    """Credential for an external AI agent (MCP / REST). Only the sha256 of the key is stored."""
    id: Optional[int] = Field(default=None, primary_key=True)
    allowance_id: int = Field(foreign_key="allowance.id", index=True)
    name: str
    key_prefix: str
    key_hash: str = Field(index=True, unique=True)
    per_purchase_cap: Optional[float] = None
    created_at: datetime = Field(default_factory=utcnow, sa_type=TZ)
    last_used_at: Optional[datetime] = Field(default=None, sa_type=TZ)
    revoked_at: Optional[datetime] = Field(default=None, sa_type=TZ)
