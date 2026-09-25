from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-5-mini")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.llm_model == "gpt-5-mini"
    assert settings.log_level == "DEBUG"
