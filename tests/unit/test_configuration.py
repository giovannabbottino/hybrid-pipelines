import pytest
from dotenv import dotenv_values

from hybrid_pipelines.configuration import PipelineSettings, float_env, int_env
from hybrid_pipelines.infrastructure.ollama_client import OllamaClientConfig


@pytest.mark.parametrize("value", ["", "invalid", "nan", "inf", "-inf"])
def test_invalid_numeric_environment_uses_default(monkeypatch, value):
    monkeypatch.setenv("TEST_NUMBER", value)
    assert int_env("TEST_NUMBER", 3) == 3
    assert float_env("TEST_NUMBER", 2.5) == 2.5
    assert float_env("TEST_NUMBER") is None


def test_settings_preserve_zero_and_apply_pipeline_limits(monkeypatch):
    monkeypatch.setenv("TEST_NUMBER", "0")
    monkeypatch.setenv("MCP_MAX_TURNS", "0")
    monkeypatch.setenv("MCP_MAX_TOOL_CALLS", "-10")
    monkeypatch.setenv("ANALYZE_TIMEOUT_SECONDS", "0.25")
    monkeypatch.setenv("ANALYZE_LOG_PATH", "")
    settings = PipelineSettings.from_env()
    assert int_env("TEST_NUMBER", 3) == 0
    assert float_env("TEST_NUMBER", 2.5) == 0.0
    assert settings.max_turns == 1
    assert settings.max_tool_calls == 1
    assert settings.analyze_timeout_seconds == 1.0
    assert settings.analyze_log_path == ""


def test_example_configuration_sets_requested_generation_profile(monkeypatch):
    for key, value in dotenv_values(".env.example").items():
        if value is not None:
            monkeypatch.setenv(key, value)
    config = OllamaClientConfig.from_env()
    assert config.timeout_seconds == 300
    assert config.options == {
        "seed": 42,
        "temperature": 0,
        "top_k": 1,
        "top_p": 1,
        "min_p": 0,
        "num_predict": 2048,
        "num_ctx": 8192,
    }
    assert config.jsonl_path.as_posix() == "logs/ollama_responses.jsonl"
    assert PipelineSettings.from_env().analyze_log_path == "logs/analyze_log.jsonl"


def test_log_defaults_use_logs_directory(monkeypatch):
    monkeypatch.delenv("OLLAMA_JSONL_PATH", raising=False)
    monkeypatch.delenv("ANALYZE_LOG_PATH", raising=False)
    assert OllamaClientConfig.from_env().jsonl_path.as_posix() == "logs/ollama_responses.jsonl"
    assert PipelineSettings.from_env().analyze_log_path == "logs/analyze_log.jsonl"
