"""Application settings, read from the environment (or a local .env file).

Every setting has an environment variable named after it in upper case, for
example ``llm_model`` is read from ``LLM_MODEL``. Secrets are held as
``SecretStr`` so they are masked in logs, reprs and error messages; call
``get_secret_value()`` only at the point where the secret is handed to a
client library.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Bring your own AI. Any LiteLLM model string works, for example
    # "anthropic/claude-haiku-5-5", "openai/<model>", "azure/<deployment>",
    # "gemini/<model>", "bedrock/<model id>" or "ollama/<model>".
    llm_model: str = "anthropic/claude-haiku-5-5"
    llm_api_key: SecretStr | None = None
    llm_api_base: str | None = Field(
        default=None,
        description="Override the provider endpoint, for Azure OpenAI or a local Ollama server.",
    )
    llm_temperature: float = 0.0

    # Spending and rate limits, so a public demo cannot run up a bill.
    spend_cap_usd: float = Field(default=5.0, ge=0)
    rate_limit_per_minute: int = Field(default=30, ge=1)

    # Copilot database.
    database_url: SecretStr = SecretStr("postgresql://copilot:copilot@localhost:5432/copilot")

    # Ticket system and test mail server.
    zammad_url: str = "http://localhost:8080"
    zammad_token: SecretStr | None = None
    smtp_host: str = "localhost"
    smtp_port: int = 1025


@lru_cache
def get_settings() -> Settings:
    return Settings()
