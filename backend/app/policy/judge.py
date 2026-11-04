"""Policy Judge: a pure, deterministic rule engine. No LLM, no I/O."""

from decimal import Decimal

from app.policy.schema import Check, Policy, SpendContext, SpendRequest, Verdict


def _d(x: float) -> Decimal:
    """Exact cents, so 'exactly at the limit' is never lost to float error."""
    return Decimal(str(x)).quantize(Decimal("0.01"))


def _m(x: Decimal) -> str:
    return f"${x:,.2f}"


def judge(
    policy: Policy, spend: SpendRequest, context: SpendContext, skip_approval: bool = False
) -> Verdict:
    """skip_approval: the user already approved this spend; every FAIL rule still applies."""
    amount = _d(spend.amount)
    spent_today = _d(context.spent_today)
    remaining = _d(context.allowance_remaining)
    category = spend.category
    cat_label = category.capitalize()
    merchant = spend.merchant_name.strip().lower()

    checks: list[Check] = []

    def add(rule: str, failed: bool, fail_detail: str, pass_detail: str) -> None:
        checks.append(Check(rule=rule, passed=not failed, detail=fail_detail if failed else pass_detail))

    add(
        "allowance_remaining",
        amount > remaining,
        f"{_m(amount)} is more than the {_m(remaining)} left in your allowance",
        f"{_m(amount)} fits within the {_m(remaining)} left in your allowance",
    )
    add(
        "blocked_merchant",
        merchant in {m.strip().lower() for m in policy.blocked_merchants},
        f"{spend.merchant_name} is blocked by your rules",
        f"{spend.merchant_name} is not a blocked merchant",
    )
    add(
        "blocked_category",
        category in policy.blocked_categories,
        f"{cat_label} is blocked by your rules",
        f"{cat_label} is not a blocked category",
    )
    if category in policy.blocked_categories:
        # Already reported by blocked_category; don't give a duplicate reason.
        checks.append(
            Check(rule="allowed_category", passed=True, detail=f"Not checked: {cat_label} is already blocked")
        )
    else:
        add(
            "allowed_category",
            bool(policy.allowed_categories) and category not in policy.allowed_categories,
            f"{cat_label} is not in your allowed categories ({', '.join(policy.allowed_categories)})",
            f"{cat_label} is an allowed category",
        )
    per_tx = _d(policy.per_tx_max)
    add(
        "per_tx_max",
        amount > per_tx,
        f"{_m(amount)} is over your {_m(per_tx)} per-purchase limit",
        f"{_m(amount)} is within your {_m(per_tx)} per-purchase limit",
    )
    daily = _d(policy.daily_max)
    add(
        "daily_max",
        spent_today + amount > daily,
        f"{_m(amount)} on top of {_m(spent_today)} already spent today goes over your {_m(daily)} daily limit",
        f"{_m(spent_today + amount)} spent today stays within your {_m(daily)} daily limit",
    )
    if spend.channel == "offline":
        tap = _d(policy.offline.per_tap_max)
        if not policy.offline.enabled:
            add("offline_limit", True, "Offline payments are turned off in your rules", "")
        else:
            add(
                "offline_limit",
                amount > tap,
                f"{_m(amount)} is over your {_m(tap)} offline per-tap limit",
                f"{_m(amount)} is within your {_m(tap)} offline per-tap limit",
            )

    failed = [c for c in checks if not c.passed]
    if failed:
        return Verdict(decision="FAIL", reasons=[c.detail for c in failed], checks=checks)

    threshold = _d(policy.require_approval_above)
    over = amount > threshold
    needs_approval = over and not skip_approval
    if needs_approval:
        detail = f"{_m(amount)} is above your {_m(threshold)} approval threshold, so you need to approve it"
    elif over:
        detail = f"{_m(amount)} is above your {_m(threshold)} approval threshold, but you approved it"
    else:
        detail = f"{_m(amount)} is within your {_m(threshold)} approval threshold"
    checks.append(Check(rule="require_approval_above", passed=not needs_approval, detail=detail))
    if needs_approval:
        return Verdict(decision="NEEDS_APPROVAL", reasons=[detail], checks=checks)
    return Verdict(decision="PASS", reasons=["All of your rules are satisfied"], checks=checks)
