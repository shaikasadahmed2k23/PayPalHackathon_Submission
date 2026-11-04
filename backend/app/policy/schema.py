from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, BeforeValidator, Field

Category = Literal[
    "food",
    "groceries",
    "transport",
    "entertainment",
    "clothing",
    "health",
    "electronics",
    "alcohol",
    "gambling",
    "other",
]
CATEGORIES: list[str] = list(get_args(Category))


def _norm(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


# A category that is lowercased, then checked against the fixed vocabulary.
Cat = Annotated[Category, BeforeValidator(_norm)]
Money = Annotated[float, Field(ge=0)]


class OfflinePolicy(BaseModel):
    enabled: bool = True
    per_tap_max: Money = 5.0


class Policy(BaseModel):
    currency: str = "USD"
    per_tx_max: Money = 20.0
    daily_max: Money = 60.0
    require_approval_above: Money = 10.0
    allowed_categories: list[Cat] = Field(default_factory=list)  # empty = all allowed
    blocked_categories: list[Cat] = Field(default_factory=list)
    blocked_merchants: list[str] = Field(default_factory=list)
    offline: OfflinePolicy = Field(default_factory=OfflinePolicy)


class SpendRequest(BaseModel):
    amount: Annotated[float, Field(gt=0)]
    category: Cat
    merchant_name: str
    channel: Literal["online", "offline"]


class SpendContext(BaseModel):
    spent_today: Money
    allowance_remaining: Money


class Check(BaseModel):
    rule: str
    passed: bool
    detail: str


class Verdict(BaseModel):
    decision: Literal["PASS", "NEEDS_APPROVAL", "FAIL"]
    reasons: list[str]
    checks: list[Check]
