"""Call each configured LLM provider once with a tiny structured-output request.

Run from backend/:  python scripts/check_llm.py
Prints "<provider> OK (<model>)" or the exact error. Never prints keys."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm import _attempt, _has_key  # noqa: E402


class Ping(BaseModel):
    ok: bool
    word: str


def main() -> int:
    s = get_settings()
    failed = False
    for provider, model in (("gemini", s.gemini_model), ("groq", s.groq_model)):
        if not _has_key(provider):
            print(f"{provider} SKIPPED (no API key set)")
            continue
        try:
            r = _attempt(provider, "Reply as JSON.", 'Return {"ok": true, "word": "pong"}.', Ping)
            print(f"{provider} OK ({model}) -> {r.model_dump()}")
        except Exception as e:  # noqa: BLE001
            failed = True
            print(f"{provider} FAILED ({model}): {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
