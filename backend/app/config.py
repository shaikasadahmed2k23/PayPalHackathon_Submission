from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"

    database_url: str = "sqlite:///./leashpay.db"

    llm_provider: str = "gemini"  # primary: gemini | groq
    llm_fallback: str = ""  # secondary; "" = the other provider (if its key is set), "none" = off
    gemini_api_key: str = ""
    groq_api_key: str = ""
    server_signing_key: str = ""  # base64 32-byte Ed25519 seed
    frontend_url: str = "http://localhost:3000"
    app_env: str = "development"  # "production" makes SERVER_SIGNING_KEY mandatory
    cors_origins: str = "http://localhost:3000"  # comma-separated list of allowed browser origins
    merchant_payout_email: str = ""  # sandbox business account that receives all demo payouts

    gemini_model: str = "gemini-3.5-flash-lite"
    groq_model: str = "openai/gpt-oss-120b"

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        """Comma-separated CORS_ORIGINS; trailing slashes stripped (browsers send origins without one)."""
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


def check_production_settings(settings: Settings) -> None:
    """Fail fast: in production a missing signing key would silently mint a throwaway key per deploy."""
    if settings.is_production and not settings.server_signing_key:
        raise RuntimeError(
            "APP_ENV=production requires SERVER_SIGNING_KEY (generate one: python scripts/gen_signing_key.py)"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
