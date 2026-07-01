"""Provider capability and diagnostic regression tests."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from src.providers.capabilities import get_provider_capabilities
from src.providers.llm import build_llm, provider_diagnostics


def test_provider_diagnostics_redacts_secrets_and_proxy_values() -> None:
    """Doctor output must be useful without leaking keys or proxy credentials."""
    import src.providers.llm as llm_mod

    llm_mod._dotenv_loaded = True
    env = {
        "LLM_PROVIDER": "deepseek",
        "LLM_MODEL_NAME": "deepseek-v4-pro",
        "DEEPSEEK_API_KEY": "sk-super-secret",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1?token=secret",
        "HTTPS_PROXY": "http://user:pass@proxy.local:8888",
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "TIMEOUT_SECONDS": "7",
        "MAX_RETRIES": "5",
    }

    with patch.dict(os.environ, env, clear=True):
        diagnostics = provider_diagnostics()

    encoded = json.dumps(diagnostics, sort_keys=True)
    assert diagnostics["provider"] == "deepseek"
    assert diagnostics["model"] == "deepseek-v4-pro"
    assert diagnostics["base_url"] == "https://api.deepseek.com"
    assert diagnostics["timeout_seconds"] == 7
    assert diagnostics["max_retries"] == 5
    assert diagnostics["api_key"]["DEEPSEEK_API_KEY"] == "set"
    assert diagnostics["proxy"]["HTTPS_PROXY"] == "http://proxy.local:8888"
    assert diagnostics["proxy"]["NO_PROXY"] == "set"
    assert "openai" in diagnostics["packages"]
    assert "sk-super-secret" not in encoded
    assert "user:pass" not in encoded
    assert "token=secret" not in encoded


def test_provider_capabilities_are_provider_specific() -> None:
    """DeepSeek, Kimi, Gemini, and OpenRouter should not share one mutation bag."""
    deepseek = get_provider_capabilities("deepseek", "deepseek-v4-pro")
    kimi = get_provider_capabilities("moonshot", "kimi-k2.6")
    gemini = get_provider_capabilities("gemini", "gemini-3.5-flash")
    openrouter = get_provider_capabilities("openrouter", "deepseek/deepseek-v4-pro")

    assert deepseek.capture_reasoning is True
    assert deepseek.send_reasoning_content is False
    assert deepseek.gemini_thought_signatures is False

    assert kimi.capture_reasoning is True
    assert kimi.send_reasoning_content is True
    assert kimi.default_headers["User-Agent"].startswith("Vibe-Trading/")

    assert gemini.gemini_thought_signatures is True
    assert gemini.send_reasoning_content is False

    assert openrouter.openrouter_reasoning_body is True
    assert openrouter.send_reasoning_content is False


def test_reasoning_effort_extra_body_is_openrouter_only() -> None:
    """LLM_REASONING_EFFORT should not leak into official DeepSeek payloads."""
    import src.providers.llm as llm_mod

    llm_mod._dotenv_loaded = True
    captured: dict = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    env = {
        "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "ds-test",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        "LLM_MODEL_NAME": "deepseek-v4-pro",
        "LLM_REASONING_EFFORT": "high",
        "VIBE_TRADING_DEEPSEEK_ADAPTER": "openai-compatible",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch.object(llm_mod, "ChatOpenAIWithReasoning", _FakeChatOpenAI):
            build_llm()

    assert captured["extra_body"] is None


def test_kimi_user_agent_header_is_moonshot_only() -> None:
    """Kimi whitelist headers should be scoped to Moonshot/Kimi."""
    import src.providers.llm as llm_mod

    llm_mod._dotenv_loaded = True
    captured: dict = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    env = {
        "LLM_PROVIDER": "moonshot",
        "MOONSHOT_API_KEY": "mk-test",
        "MOONSHOT_BASE_URL": "https://api.moonshot.ai/v1",
        "LLM_MODEL_NAME": "kimi-k2.6",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch.object(llm_mod, "ChatOpenAIWithReasoning", _FakeChatOpenAI):
            build_llm()

    assert captured["default_headers"]["User-Agent"].startswith("Vibe-Trading/")

    captured.clear()
    env = {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL_NAME": "gpt-4",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch.object(llm_mod, "ChatOpenAIWithReasoning", _FakeChatOpenAI):
            build_llm()

    assert "default_headers" not in captured


def test_deepseek_native_adapter_is_used_when_available(monkeypatch) -> None:
    """When the optional langchain-deepseek package is importable, the DeepSeek
    native-adapter path returns a NativeLLM pointed at the DeepSeek endpoint.

    (We no longer call ``ChatDeepSeek`` directly — the openai-compatible
    NativeLLM is functionally equivalent for our usage — but the opt-in
    adapter mode still routes through this branch when the package is present.)
    """
    import sys
    from types import SimpleNamespace

    import src.providers.llm as llm_mod
    from src.providers.llm import NativeLLM

    llm_mod._dotenv_loaded = True

    monkeypatch.setitem(sys.modules, "langchain_deepseek", SimpleNamespace(ChatDeepSeek=object))
    env = {
        "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "ds-test",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        "LLM_MODEL_NAME": "deepseek-v4-pro",
    }

    with patch.dict(os.environ, env, clear=True):
        llm = build_llm()

    assert isinstance(llm, NativeLLM)
    assert llm.model_name == "deepseek-v4-pro"
    assert llm.api_key == "ds-test"
    assert llm.base_url == "https://api.deepseek.com/v1"
