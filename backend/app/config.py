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
    merchant_payout_email: str = ""  # sandbox business account that receives all demo payouts

    gemini_model: str = "gemini-2.5-flash"
    groq_model: str = "llama-3.3-70b-versatile"


@lru_cache
def get_settings() -> Settings:
    return Settings()
