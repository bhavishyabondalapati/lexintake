from app.config import Settings


def test_cors_origins_parse_comma_separated_list(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, http://localhost:3001")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:3001"]


def test_both_model_names_come_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL_CLASSIFY", "cheap-model")
    monkeypatch.setenv("ANTHROPIC_MODEL_EXTRACT", "strong-model")

    settings = Settings(_env_file=None)

    assert settings.anthropic_model_classify == "cheap-model"
    assert settings.anthropic_model_extract == "strong-model"


def test_no_model_name_is_built_in(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL_CLASSIFY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL_EXTRACT", raising=False)

    settings = Settings(_env_file=None)

    assert settings.anthropic_model_classify == settings.anthropic_model_extract == ""


def test_extraction_is_off_after_parse_by_default(monkeypatch):
    monkeypatch.delenv("EXTRACT_ON_PARSE", raising=False)

    assert Settings(_env_file=None).extract_on_parse is False
