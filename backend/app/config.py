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

    llm_provider: str = "gemini"  # gemini | groq
    gemini_api_key: str = ""
    groq_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
