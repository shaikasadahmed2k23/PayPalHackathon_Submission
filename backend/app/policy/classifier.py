from pydantic import BaseModel

from app.llm import LLMError, generate_json
from app.policy.schema import CATEGORIES, Cat

_cache: dict[str, str] = {}

_SYSTEM = (
    "You classify a product or merchant name into exactly one spending category. "
    f"Allowed categories: {', '.join(CATEGORIES)}. "
    'Return JSON: {"category": "<one of the allowed categories>"}. '
    'If nothing fits, use "other".'
)


class _Result(BaseModel):
    category: Cat


def classify_category(item_or_merchant_name: str) -> str:
    key = item_or_merchant_name.strip().lower()
    if key in _cache:
        return _cache[key]
    try:
        category = generate_json(_SYSTEM, f"Name: {item_or_merchant_name}", _Result).category
    except LLMError:
        return "other"  # not cached, so a later call can retry
    _cache[key] = category
    return category
