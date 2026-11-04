import json

from pydantic import BaseModel, Field

from app.llm import generate_json
from app.policy.schema import CATEGORIES, Policy


class _LLMParse(BaseModel):
    policy: Policy
    ambiguities: list[str] = Field(default_factory=list)


class ParsedPolicy(BaseModel):
    policy: Policy
    summary: list[str]
    ambiguities: list[str]


_EXAMPLES = [
    (
        "Food and transport only, max $15 per purchase, never alcohol, ask me above $8",
        {
            "policy": {
                "currency": "USD",
                "per_tx_max": 15.0,
                "daily_max": 60.0,
                "require_approval_above": 8.0,
                "allowed_categories": ["food", "transport"],
                "blocked_categories": ["alcohol"],
                "blocked_merchants": [],
                "offline": {"enabled": True, "per_tap_max": 5.0},
            },
            "ambiguities": ["No daily limit given; assumed $60."],
        },
    ),
    (
        "No electronics or gambling. Spend at most $40 a day. Offline taps up to $3.",
        {
            "policy": {
                "currency": "USD",
                "per_tx_max": 20.0,
                "daily_max": 40.0,
                "require_approval_above": 10.0,
                "allowed_categories": [],
                "blocked_categories": ["electronics", "gambling"],
                "blocked_merchants": [],
                "offline": {"enabled": True, "per_tap_max": 3.0},
            },
            "ambiguities": [
                "No per-purchase limit given; assumed $20.",
                "No approval threshold given; assumed $10.",
            ],
        },
    ),
    (
        "Groceries and medicine only, nothing from Lucky Casino, no offline payments, "
        "anything over $25 needs my OK",
        {
            "policy": {
                "currency": "USD",
                "per_tx_max": 20.0,
                "daily_max": 60.0,
                "require_approval_above": 25.0,
                "allowed_categories": ["groceries", "health"],
                "blocked_categories": [],
                "blocked_merchants": ["Lucky Casino"],
                "offline": {"enabled": False, "per_tap_max": 5.0},
            },
            "ambiguities": [
                "Approval threshold ($25) is above the per-purchase limit ($20), "
                "so nothing can ever need approval.",
                "'Medicine' was mapped to the 'health' category.",
            ],
        },
    ),
]

SYSTEM_PROMPT = f"""You convert a person's plain-English spending rules into a policy JSON.

Output JSON with two keys: "policy" and "ambiguities".

policy schema (all amounts are non-negative USD numbers):
{json.dumps(Policy.model_json_schema())}

Allowed category values (use ONLY these, lowercase): {", ".join(CATEGORIES)}.
Map synonyms onto them (medicine -> health, snacks -> food, taxi/bus -> transport).

Rules:
- Only set what the user actually said. For anything unspecified use these defaults:
  per_tx_max 20, daily_max 60, require_approval_above 10, allowed_categories [],
  blocked_categories [], blocked_merchants [], offline {{"enabled": true, "per_tap_max": 5}}.
- allowed_categories empty means "everything allowed except blocked".
- "Never X" / "no X" goes in blocked_categories (or blocked_merchants for a named store).
- "X only" goes in allowed_categories.
- "ambiguities": short plain-English notes about every default you assumed, anything
  you had to guess or map, and any contradictions. Empty list if there are none.
- Never invent limits the user did not state beyond the defaults above.

Examples:
{chr(10).join(f"Input: {t}{chr(10)}Output: {json.dumps(o)}{chr(10)}" for t, o in _EXAMPLES)}"""


def _money(x: float) -> str:
    return f"${x:,.2f}"


def build_summary(p: Policy) -> list[str]:
    """Human-readable bullets, built from the validated policy (not from the LLM)."""
    lines = [
        f"Up to {_money(p.per_tx_max)} per purchase",
        f"Up to {_money(p.daily_max)} per day",
        f"Ask for your approval above {_money(p.require_approval_above)}",
    ]
    if p.allowed_categories:
        lines.append("Only these categories: " + ", ".join(p.allowed_categories))
    else:
        lines.append("All categories allowed except the blocked ones")
    if p.blocked_categories:
        lines.append("Blocked categories: " + ", ".join(p.blocked_categories))
    if p.blocked_merchants:
        lines.append("Blocked merchants: " + ", ".join(p.blocked_merchants))
    if p.offline.enabled:
        lines.append(f"Offline payments allowed, up to {_money(p.offline.per_tap_max)} per tap")
    else:
        lines.append("Offline payments are turned off")
    return lines


def parse_rules(text: str) -> ParsedPolicy:
    result = generate_json(SYSTEM_PROMPT, f"Input: {text}\nOutput:", _LLMParse)
    return ParsedPolicy(
        policy=result.policy,
        summary=build_summary(result.policy),
        ambiguities=result.ambiguities,
    )
