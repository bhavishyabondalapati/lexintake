from app.config import Settings


def test_cors_origins_parse_comma_separated_list(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, http://localhost:3001")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:3001"]


def test_anthropic_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-model-name")

    assert Settings(_env_file=None).anthropic_model == "some-model-name"
