from copilot.config import Settings


def test_api_key_is_masked(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    settings = Settings(_env_file=None)
    assert settings.llm_api_key is not None
    assert "sk-test" not in repr(settings)
    assert "sk-test" not in str(settings.model_dump())
    assert settings.llm_api_key.get_secret_value() == "sk-test-not-a-real-key"


def test_defaults_to_cheap_fast_model():
    settings = Settings(_env_file=None)
    assert settings.llm_model == "anthropic/claude-haiku-5-5"
    assert settings.spend_cap_usd > 0
