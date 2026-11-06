from app.policy.judge import judge
from app.policy.schema import OfflinePolicy, Policy, SpendContext, SpendRequest


def make_policy(**kw) -> Policy:
    base = dict(
        per_tx_max=20.0,
        daily_max=60.0,
        require_approval_above=10.0,
        allowed_categories=[],
        blocked_categories=["alcohol", "electronics"],
        blocked_merchants=["Shady Shop"],
        offline=OfflinePolicy(enabled=True, per_tap_max=5.0),
    )
    base.update(kw)
    return Policy(**base)


def spend(amount=5.0, category="food", merchant="Cafe", channel="online") -> SpendRequest:
    return SpendRequest(amount=amount, category=category, merchant_name=merchant, channel=channel)


def ctx(spent_today=0.0, remaining=50.0) -> SpendContext:
    return SpendContext(spent_today=spent_today, allowance_remaining=remaining)


def failed_rules(v) -> set[str]:
    return {c.rule for c in v.checks if not c.passed}


def test_pass():
    v = judge(make_policy(), spend(5), ctx())
    assert v.decision == "PASS"
    assert v.reasons


def test_needs_approval_above_threshold():
    v = judge(make_policy(), spend(12), ctx())
    assert v.decision == "NEEDS_APPROVAL"
    assert "approval" in v.reasons[0]


def test_exactly_at_approval_threshold_passes():
    assert judge(make_policy(), spend(10), ctx()).decision == "PASS"


def test_fail_over_allowance_remaining():
    v = judge(make_policy(), spend(8), ctx(remaining=7.99))
    assert v.decision == "FAIL"
    assert failed_rules(v) == {"allowance_remaining"}


def test_exactly_allowance_remaining_allowed():
    assert judge(make_policy(), spend(8), ctx(remaining=8.0)).decision == "PASS"


def test_fail_blocked_merchant_case_insensitive():
    v = judge(make_policy(), spend(5, merchant="  shady SHOP "), ctx())
    assert v.decision == "FAIL"
    assert failed_rules(v) == {"blocked_merchant"}


def test_fail_blocked_category():
    v = judge(make_policy(), spend(5, category="electronics"), ctx())
    assert v.decision == "FAIL"
    assert v.reasons == ["Electronics is blocked by your rules"]


def test_fail_not_in_allowed_categories():
    p = make_policy(allowed_categories=["food", "transport"])
    v = judge(p, spend(5, category="clothing"), ctx())
    assert v.decision == "FAIL"
    assert failed_rules(v) == {"allowed_category"}


def test_empty_allowed_categories_allows_everything_unblocked():
    assert judge(make_policy(), spend(5, category="clothing"), ctx()).decision == "PASS"


def test_fail_over_per_tx_max():
    v = judge(make_policy(), spend(25), ctx())
    assert v.decision == "FAIL"
    assert v.reasons == ["$25.00 is over your $20.00 per-purchase limit"]


def test_exactly_per_tx_max_is_not_a_fail():
    v = judge(make_policy(), spend(20), ctx())
    assert v.decision == "NEEDS_APPROVAL"  # passes hard limits, but above $10


def test_fail_daily_max():
    v = judge(make_policy(), spend(15), ctx(spent_today=50))
    assert v.decision == "FAIL"
    assert failed_rules(v) == {"daily_max"}


def test_exactly_daily_max_allowed():
    v = judge(make_policy(), spend(10), ctx(spent_today=50))
    assert v.decision == "PASS"


def test_float_noise_at_daily_boundary():
    v = judge(make_policy(daily_max=0.3), spend(0.2), ctx(spent_today=0.1))
    assert v.decision == "PASS"


def test_fail_offline_over_per_tap():
    v = judge(make_policy(), spend(6, channel="offline"), ctx())
    assert v.decision == "FAIL"
    assert failed_rules(v) == {"offline_limit"}


def test_offline_exactly_per_tap_passes():
    assert judge(make_policy(), spend(5, channel="offline"), ctx()).decision == "PASS"


def test_online_ignores_offline_limit():
    assert judge(make_policy(), spend(8, channel="online"), ctx()).decision == "PASS"


def test_offline_disabled_fails():
    p = make_policy(offline=OfflinePolicy(enabled=False, per_tap_max=5.0))
    v = judge(p, spend(1, channel="offline"), ctx())
    assert v.decision == "FAIL"
    assert failed_rules(v) == {"offline_limit"}


def test_multiple_failures_are_all_reported():
    v = judge(make_policy(), spend(25, category="alcohol", merchant="Shady Shop", channel="offline"),
              ctx(spent_today=50, remaining=10))
    assert v.decision == "FAIL"
    assert failed_rules(v) == {
        "allowance_remaining", "blocked_merchant", "blocked_category",
        "per_tx_max", "daily_max", "offline_limit",
    }
    assert len(v.reasons) == 6


def test_blocked_category_does_not_also_report_not_allowed():
    p = make_policy(allowed_categories=["food"], blocked_categories=["alcohol"])
    v = judge(p, spend(5, category="alcohol"), ctx())
    assert v.reasons == ["Alcohol is blocked by your rules"]


def test_skip_approval_passes_but_fail_rules_still_apply():
    assert judge(make_policy(), spend(12), ctx(), skip_approval=True).decision == "PASS"
    v = judge(make_policy(), spend(25), ctx(), skip_approval=True)
    assert v.decision == "FAIL"


def test_fail_beats_needs_approval():
    v = judge(make_policy(), spend(25), ctx())
    assert v.decision == "FAIL"


def test_checks_listed_in_spec_order():
    v = judge(make_policy(), spend(5, channel="offline"), ctx())
    assert [c.rule for c in v.checks] == [
        "blocked_merchant", "blocked_category", "allowed_category",
        "per_tx_max", "offline_limit", "daily_max", "allowance_remaining", "require_approval_above",
    ]


def test_reasons_are_ordered_rules_then_limits_then_allowance():
    p = make_policy(allowed_categories=["food"], blocked_merchants=["Cafe"], offline=OfflinePolicy(enabled=True, per_tap_max=5.0))
    v = judge(p, spend(100, category="electronics", merchant="Cafe", channel="offline"), ctx(spent_today=50, remaining=10))
    kinds = [c.rule for c in v.checks if not c.passed]
    assert kinds == ["blocked_merchant", "blocked_category", "per_tx_max", "offline_limit", "daily_max", "allowance_remaining"]
    assert v.reasons[0] == "Cafe is blocked by your rules" and "left in your allowance" in v.reasons[-1]


def test_offline_disabled_is_a_rule_violation_listed_before_limits():
    p = make_policy(offline=OfflinePolicy(enabled=False, per_tap_max=5.0))
    v = judge(p, spend(100, channel="offline"), ctx())
    assert v.reasons[0] == "Offline payments are turned off in your rules" and "per-purchase" in v.reasons[1]


def test_agent_cap_is_a_limit_after_rule_violations_and_before_other_limits():
    p = make_policy(per_tx_max=4.0)
    v = judge(p, spend(6, category="alcohol"), ctx(), agent_cap=5, agent_name="Bot")
    assert v.reasons == ["Alcohol is blocked by your rules", "Over the $5.00 cap you set for Bot",
                         "$6.00 is over your $4.00 per-purchase limit"]
    assert judge(p, spend(3), ctx(), agent_cap=5, agent_name="Bot").decision == "PASS"
    assert judge(p, spend(5.0), ctx(), agent_cap=5, agent_name="Bot").decision == "FAIL"  # per_tx_max 4
    assert judge(make_policy(), spend(5.0), ctx(), agent_cap=5, agent_name="Bot").decision == "PASS"
