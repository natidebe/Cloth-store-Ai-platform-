from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-5-mini")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.llm_model == "gpt-5-mini"
    assert settings.log_level == "DEBUG"


def test_supabase_rest_url_is_trimmed_to_project_url(monkeypatch):
    for raw in ["https://abc.supabase.co/rest/v1/", "https://abc.supabase.co/", " https://abc.supabase.co "]:
        monkeypatch.setenv("SUPABASE_URL", raw)
        assert Settings(_env_file=None).supabase_url == "https://abc.supabase.co"
