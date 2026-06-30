"""LLM factory and native OpenAI-compatible client.

Replaces the previous ``ChatOpenAIWithReasoning`` subclass of langchain's
``ChatOpenAI`` with a direct implementation on top of the ``openai`` SDK.
All provider quirks previously implemented by monkey-patching langchain
internals (``_convert_dict_to_message``, ``_convert_chunk_to_generation_chunk``,
``_get_request_payload``, ``_convert_input``) are now handled here directly,
without the langchain translation layer.
"""

from __future__ import annotations

import logging
import os
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import urlsplit

from src.providers.capabilities import get_provider_capabilities, provider_env_names
from src.providers.messages import AIMessage, AIMessageChunk

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - openai is a hard dependency now
    OpenAI = None  # type: ignore


AGENT_DIR = Path(__file__).resolve().parents[2]

# .env search order: ~/.vibe-trading/.env → agent/.env → $CWD/.env
_ENV_CANDIDATES = [
    Path.home() / ".vibe-trading" / ".env",
    AGENT_DIR / ".env",
    Path.cwd() / ".env",
]

_ENV_LABELS = ("~/.vibe-trading/.env", "<AGENT_DIR>/.env", "<CWD>/.env")

logger = logging.getLogger(__name__)

_dotenv_loaded: bool = False


# ---------------------------------------------------------------------------
# Provider quirks helpers (ported verbatim from ChatOpenAIWithReasoning)
# ---------------------------------------------------------------------------


def _extract_tool_call_thought_signature(tool_call: Any) -> Optional[str]:
    if not isinstance(tool_call, dict):
        return None

    extra_content = tool_call.get("extra_content")
    if isinstance(extra_content, dict):
        google = extra_content.get("google")
        if isinstance(google, dict):
            value = google.get("thought_signature") or google.get("thoughtSignature")
            if value:
                return value

    function = tool_call.get("function")
    containers = [tool_call]
    if isinstance(function, dict):
        containers.append(function)
    for container in containers:
        value = container.get("thought_signature") or container.get("thoughtSignature")
        if value:
            return value
    return None


def _collect_tool_call_thought_signatures(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []

    signatures: list[dict[str, Any]] = []
    for fallback_index, tool_call in enumerate(tool_calls):
        signature = _extract_tool_call_thought_signature(tool_call)
        if not signature or not isinstance(tool_call, dict):
            continue

        index = tool_call.get("index")
        entry: dict[str, Any] = {
            "index": index if isinstance(index, int) else fallback_index,
            "thought_signature": signature,
        }
        if tool_call.get("id"):
            entry["id"] = tool_call["id"]
        signatures.append(entry)
    return signatures


def _signature_maps(message: AIMessage) -> tuple[dict[str, str], dict[int, str]]:
    by_id: dict[str, str] = {}
    by_index: dict[int, str] = {}
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}

    entries = additional_kwargs.get("tool_call_thought_signatures", [])
    if isinstance(entries, dict):
        entries = [entries]
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            signature = entry.get("thought_signature")
            if not signature:
                continue
            if entry.get("id"):
                by_id[str(entry["id"])] = signature
            index = entry.get("index")
            if isinstance(index, int):
                by_index[index] = signature

    raw_tool_calls = additional_kwargs.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        for index, tool_call in enumerate(raw_tool_calls):
            signature = _extract_tool_call_thought_signature(tool_call)
            if not signature or not isinstance(tool_call, dict):
                continue
            if tool_call.get("id"):
                by_id[str(tool_call["id"])] = signature
            by_index[index] = signature

    return by_id, by_index


def _set_tool_call_thought_signature(tool_call: Any, signature: str) -> None:
    if not isinstance(tool_call, dict):
        return
    extra_content = tool_call.get("extra_content")
    if not isinstance(extra_content, dict):
        extra_content = {}
        tool_call["extra_content"] = extra_content
    google = extra_content.get("google")
    if not isinstance(google, dict):
        google = {}
        extra_content["google"] = google
    google["thought_signature"] = signature


def _inject_tool_call_thought_signatures(outbound: Any, source_message: AIMessage) -> None:
    if not isinstance(outbound, list):
        return

    by_id, by_index = _signature_maps(source_message)
    if not by_id and not by_index:
        return

    for index, tool_call in enumerate(outbound):
        signature = None
        if isinstance(tool_call, dict) and tool_call.get("id"):
            signature = by_id.get(str(tool_call["id"]))
        signature = signature or by_index.get(index)
        if signature:
            _set_tool_call_thought_signature(tool_call, signature)


def _strip_tool_call_extra_content(outbound: Any) -> None:
    if not isinstance(outbound, list):
        return
    for tool_call in outbound:
        if isinstance(tool_call, dict):
            tool_call.pop("extra_content", None)


# ---------------------------------------------------------------------------
# Inbound conversion: raw OpenAI dict -> AIMessage
# ---------------------------------------------------------------------------


def _capture_inbound(src: Any, caps: Any) -> dict[str, Any]:
    """Extract provider-specific fields from a raw OpenAI message dict.

    Returns the additional_kwargs that should land on the AIMessage.
    """
    additional_kwargs: dict[str, Any] = {}
    if not isinstance(src, dict):
        return additional_kwargs
    if caps.capture_reasoning and (value := src.get("reasoning_content") or src.get("reasoning")):
        additional_kwargs["reasoning_content"] = value
    if caps.gemini_thought_signatures and (
        signatures := _collect_tool_call_thought_signatures(src.get("tool_calls"))
    ):
        additional_kwargs["tool_call_thought_signatures"] = signatures
    return additional_kwargs


def _normalize_tool_call(tc: Any, caps: Any) -> Optional[dict[str, Any]]:
    """Convert an OpenAI-format tool_call dict to the langchain shape used by
    ChatLLM._parse_response (``{"id","name","args"}``).

    Returns None for malformed entries.
    """
    if not isinstance(tc, dict):
        return None
    function = tc.get("function") or {}
    if not isinstance(function, dict):
        return None
    import json as _json

    raw_args = function.get("arguments")
    if isinstance(raw_args, str):
        try:
            args: Any = _json.loads(raw_args) if raw_args else {}
        except _json.JSONDecodeError:
            args = {"raw": raw_args}
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        args = {}

    entry: dict[str, Any] = {
        "id": tc.get("id") or "",
        "name": function.get("name") or "",
        "args": args,
    }

    # Preserve thought signature in additional_kwargs shape if present
    if caps.gemini_thought_signatures:
        signature = _extract_tool_call_thought_signature(tc)
        if signature:
            entry["thought_signature"] = signature
    return entry


def _convert_choice_message(choice_message: Any, caps: Any, finish_reason: str) -> AIMessage:
    """Build an AIMessage from a non-streaming choice["message"] dict."""
    if not isinstance(choice_message, dict):
        return AIMessage(content="", response_metadata={"finish_reason": finish_reason})

    raw_tool_calls = choice_message.get("tool_calls") or []
    tool_calls = [
        normalized
        for normalized in (_normalize_tool_call(tc, caps) for tc in raw_tool_calls)
        if normalized is not None
    ]
    additional_kwargs = _capture_inbound(choice_message, caps)
    return AIMessage(
        content=choice_message.get("content") or "",
        tool_calls=tool_calls,
        additional_kwargs=additional_kwargs,
        response_metadata={"finish_reason": finish_reason},
    )


def _convert_delta(delta: Any, caps: Any, finish_reason: Optional[str]) -> AIMessageChunk:
    """Build an AIMessageChunk from a streaming choice["delta"] dict."""
    if not isinstance(delta, dict):
        return AIMessageChunk(content="", response_metadata={"finish_reason": finish_reason or "stop"})

    raw_tool_calls = delta.get("tool_calls") or []
    tool_calls = [
        normalized
        for normalized in (_normalize_tool_call(tc, caps) for tc in raw_tool_calls)
        if normalized is not None
    ]
    additional_kwargs = _capture_inbound(delta, caps)
    return AIMessageChunk(
        content=delta.get("content") or "",
        tool_calls=tool_calls,
        additional_kwargs=additional_kwargs,
        response_metadata={"finish_reason": finish_reason or "stop"},
    )


# ---------------------------------------------------------------------------
# Outbound conversion: AIMessage list -> OpenAI request messages
# ---------------------------------------------------------------------------


def _to_request_messages(
    messages: Iterable[Any],
    *,
    caps: Any,
) -> list[dict[str, Any]]:
    """Convert input messages (dicts or AIMessage) to OpenAI wire format.

    This is the outbound side of all provider quirks: re-inject
    reasoning_content for Moonshot/DeepSeek, normalize content=None → ""
    for strict providers, round-trip Gemini thought signatures, and strip
    extra_content for providers that reject unknown fields.
    """
    outbound: list[dict[str, Any]] = []
    for raw in messages:
        if isinstance(raw, dict):
            outbound.append(dict(raw))
        elif isinstance(raw, AIMessage):
            outbound.append(_aimessage_to_request_dict(raw, caps=caps))
        else:
            # HumanMessage / SystemMessage duck-typed
            content = getattr(raw, "content", "")
            additional = getattr(raw, "additional_kwargs", {}) or {}
            role = "assistant" if getattr(raw, "type", "") == "ai" else getattr(raw, "type", "user")
            if role == "human":
                role = "user"
            outbound.append({"role": role, "content": content or "", **additional})

    for m in outbound:
        if m.get("role") != "assistant":
            continue
        if caps.normalize_assistant_content and m.get("content") is None:
            m["content"] = ""
        additional = m.pop("additional_kwargs", None) or {}
        source_signatures = additional.get("tool_call_thought_signatures")
        if source_signatures:
            # _signature_maps reads from additional_kwargs — re-attach temporarily
            synthetic = AIMessage(additional_kwargs={"tool_call_thought_signatures": source_signatures})
            if caps.gemini_thought_signatures:
                _inject_tool_call_thought_signatures(m.get("tool_calls"), synthetic)
            else:
                _strip_tool_call_extra_content(m.get("tool_calls"))
        if caps.send_reasoning_content:
            # Moonshot kimi-k2.6 requires reasoning_content on every assistant
            # turn; inject "" when absent so continuations are accepted.
            m["reasoning_content"] = additional.get("reasoning_content", "")
        else:
            m.pop("reasoning_content", None)
        if not caps.gemini_thought_signatures:
            _strip_tool_call_extra_content(m.get("tool_calls"))
    return outbound


def _aimessage_to_request_dict(message: AIMessage, *, caps: Any) -> dict[str, Any]:
    import json as _json

    content = message.content
    if caps.normalize_assistant_content and (content is None or content == ""):
        content = ""
    request: dict[str, Any] = {"role": "assistant", "content": content or ""}

    # Prefer the structured ``message.tool_calls`` (langchain shape: id/name/args).
    # Fall back to the raw OpenAI-format ``additional_kwargs["tool_calls"]``
    # (id/type/function:{name,arguments}) that tests and persisted history use
    # when they construct AIMessage directly.
    raw_tool_calls: list[dict[str, Any]] = []
    for tc in message.tool_calls or []:
        if isinstance(tc, dict):
            raw_tool_calls.append({
                "id": tc.get("id") or "",
                "type": "function",
                "function": {
                    "name": tc.get("name") or "",
                    "arguments": _json.dumps(tc.get("args") or {}, ensure_ascii=False),
                },
            })
    if not raw_tool_calls:
        for tc in (message.additional_kwargs or {}).get("tool_calls") or []:
            if isinstance(tc, dict) and isinstance(tc.get("function"), dict):
                raw_tool_calls.append(dict(tc))
    if raw_tool_calls:
        request["tool_calls"] = raw_tool_calls

    if message.additional_kwargs:
        request["additional_kwargs"] = dict(message.additional_kwargs)
    return request


# ---------------------------------------------------------------------------
# NativeLLM — direct replacement for ChatOpenAIWithReasoning
# ---------------------------------------------------------------------------


class NativeLLM:
    """OpenAI-compatible client with provider-quirks handling.

    Mirrors the surface ChatLLM depends on: ``bind_tools``, ``invoke``,
    ``stream``. Internally uses the ``openai`` SDK and converts responses to
    our duck-typed ``AIMessage`` / ``AIMessageChunk``.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        base_url: Optional[str] = None,
        vibe_provider: Optional[str] = None,
        extra_body: Optional[dict[str, Any]] = None,
        default_headers: Optional[dict[str, str]] = None,
        # Deprecated, kept for langchain-era callers. Ignored.
        callbacks: Any = None,
        **_legacy: Any,
    ) -> None:
        if OpenAI is None:
            raise RuntimeError("openai SDK is not installed")
        self.model_name = model
        self.model = model
        self.temperature = temperature
        self.timeout = timeout if timeout is not None else 120
        self.max_retries = max_retries if max_retries is not None else 2
        self.base_url = base_url or ""
        self.api_key = api_key or ""
        self._vibe_provider = vibe_provider or os.getenv("LANGCHAIN_PROVIDER", "openai").lower()
        self._extra_body = extra_body
        self._default_headers = dict(default_headers) if default_headers else None
        self._tools: list[dict[str, Any]] = []
        # Lazy: tests construct NativeLLM to exercise pure-conversion methods
        # (_create_chat_result, _get_request_payload, ...) without a network
        # client. Build the OpenAI client only when invoke/stream is called.
        self._client: Any = None

    def _build_client(self) -> Any:
        kwargs: dict[str, Any] = {
            "api_key": self.api_key or "ollama",
            "base_url": self.base_url or None,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }
        if self._default_headers:
            kwargs["default_headers"] = self._default_headers
        return OpenAI(**kwargs)

    def _ensure_client(self) -> Any:
        """Lazily build the OpenAI client on first network use."""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _capabilities(self) -> Any:
        return get_provider_capabilities(self._vibe_provider, str(self.model_name))

    # ------------------------------------------------------------------
    # LangChain-compatibility shims
    #
    # The legacy test suite calls internal ChatOpenAI method names directly
    # (``_create_chat_result``, ``_convert_chunk_to_generation_chunk``,
    # ``_get_request_payload``, ``_convert_input``). Rather than rewrite 700+
    # lines of regression tests, expose the same names as thin wrappers over
    # the new helpers. The business behavior they assert (reasoning_content
    # capture, thought-signature round-trip, content=None normalization) is
    # identical.
    # ------------------------------------------------------------------

    @staticmethod
    def _ns(message: Any) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(message=message)

    def _create_chat_result(self, response: Any, generation_info: Any = None) -> Any:
        from types import SimpleNamespace
        raw = response if isinstance(response, dict) else response.model_dump()
        caps = self._capabilities()
        choices = raw.get("choices") or []
        generations: list[Any] = []
        for choice in choices:
            finish_reason = choice.get("finish_reason") or "stop"
            message = _convert_choice_message(choice.get("message") or {}, caps, finish_reason)
            usage = raw.get("usage")
            if usage:
                message.response_metadata["usage"] = usage
            generations.append(SimpleNamespace(message=message))
        return SimpleNamespace(generations=generations)

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: Any,
    ) -> Any:
        from types import SimpleNamespace
        raw = chunk if isinstance(chunk, dict) else chunk.model_dump()
        caps = self._capabilities()
        choices = raw.get("choices") or []
        if not choices:
            # usage-only final chunk: return an empty chunk unchanged
            empty = AIMessageChunk(content="")
            usage = raw.get("usage")
            if usage:
                empty.response_metadata["usage"] = usage
            return SimpleNamespace(message=empty)
        choice = choices[0]
        delta = choice.get("delta") or choice.get("chunk", {}).get("delta") or {}
        finish_reason = choice.get("finish_reason")
        message = _convert_delta(delta, caps, finish_reason)
        usage = raw.get("usage")
        if usage and not message.content and not message.tool_calls:
            message.response_metadata["usage"] = usage
        return SimpleNamespace(message=message)

    def _convert_input(self, input: Any) -> Any:
        from types import SimpleNamespace
        caps = self._capabilities()
        if isinstance(input, str) or not hasattr(input, "__iter__"):
            return SimpleNamespace(to_messages=lambda: [])
        # When Gemini thought-signatures are in play, lift signatures from raw
        # OpenAI-format dicts back onto the converted AIMessage — the loop
        # replays history as dicts and the signature lives only there.
        messages: list[Any] = []
        if isinstance(input, list):
            for raw in input:
                if isinstance(raw, dict):
                    role = raw.get("role")
                    if role == "assistant":
                        additional = {}
                        if caps.capture_reasoning and (raw.get("reasoning_content") or raw.get("reasoning")):
                            additional["reasoning_content"] = raw.get("reasoning_content") or raw.get("reasoning")
                        if caps.gemini_thought_signatures and (
                            sigs := _collect_tool_call_thought_signatures(raw.get("tool_calls"))
                        ):
                            additional["tool_call_thought_signatures"] = sigs
                        tool_calls = [
                            norm for norm in (_normalize_tool_call(tc, caps) for tc in (raw.get("tool_calls") or []))
                            if norm is not None
                        ]
                        messages.append(AIMessage(
                            content=raw.get("content") or "",
                            tool_calls=tool_calls,
                            additional_kwargs=additional,
                            response_metadata={"finish_reason": "stop"},
                        ))
                    elif role == "user":
                        from src.providers.messages import HumanMessage
                        messages.append(HumanMessage(content=raw.get("content") or ""))
                    elif role == "system":
                        from src.providers.messages import SystemMessage
                        messages.append(SystemMessage(content=raw.get("content") or ""))
                    elif isinstance(raw, AIMessage):
                        messages.append(raw)
                else:
                    messages.append(raw)
        else:
            messages = list(input)

        def _to_messages() -> list[Any]:
            return messages

        return SimpleNamespace(to_messages=_to_messages)

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        caps = self._capabilities()
        messages = input_ if isinstance(input_, list) else (
            input_.to_messages() if hasattr(input_, "to_messages") else list(input_)
        )
        request_messages = _to_request_messages(messages, caps=caps)
        return {"messages": request_messages, "stop": stop}

    def bind_tools(self, tools: list[dict[str, Any]]) -> "NativeLLM":
        clone = NativeLLM(
            model=self.model_name,
            temperature=self.temperature,
            timeout=self.timeout,
            max_retries=self.max_retries,
            base_url=self.base_url,
            api_key=self.api_key,
            vibe_provider=self._vibe_provider,
            extra_body=self._extra_body,
            default_headers=self._default_headers,
        )
        clone._tools = list(tools) if tools else []
        return clone

    def _request_kwargs(
        self,
        messages: Iterable[Any],
        *,
        timeout: Optional[int],
        stream: bool,
    ) -> dict[str, Any]:
        caps = self._capabilities()
        request_messages = _to_request_messages(messages, caps=caps)
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": request_messages,
            "temperature": self.temperature,
            "stream": stream,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        if self._tools:
            kwargs["tools"] = self._tools
            kwargs["tool_choice"] = "auto"
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        return kwargs

    def invoke(
        self,
        messages: Iterable[Any],
        config: Optional[dict[str, Any]] = None,
    ) -> AIMessage:
        timeout = (config or {}).get("timeout")
        request_kwargs = self._request_kwargs(messages, timeout=timeout, stream=False)
        response = self._ensure_client().chat.completions.create(**request_kwargs)
        return self._parse_completion_response(response)

    def stream(
        self,
        messages: Iterable[Any],
        config: Optional[dict[str, Any]] = None,
    ) -> Iterator[AIMessageChunk]:
        timeout = (config or {}).get("timeout")
        request_kwargs = self._request_kwargs(messages, timeout=timeout, stream=True)
        caps = self._capabilities()
        response = self._ensure_client().chat.completions.create(**request_kwargs)
        for chunk in response:
            yield from self._iter_chunk(chunk, caps)

    def _parse_completion_response(self, response: Any) -> AIMessage:
        caps = self._capabilities()
        # response is openai ChatCompletion; model_dump() gives plain dict
        raw = response.model_dump() if hasattr(response, "model_dump") else response
        choices = raw.get("choices") or []
        if not choices:
            usage = raw.get("usage")
            return AIMessage(
                content="",
                response_metadata={"finish_reason": "stop", **({"usage": usage} if usage else {})},
            )
        choice = choices[0]
        finish_reason = choice.get("finish_reason") or "stop"
        message = _convert_choice_message(choice.get("message") or {}, caps, finish_reason)
        usage = raw.get("usage")
        if usage:
            message.response_metadata["usage"] = usage
        return message

    def _iter_chunk(self, chunk: Any, caps: Any) -> Iterable[AIMessageChunk]:
        raw = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
        choices = raw.get("choices") or []
        for choice in choices:
            delta = choice.get("delta") or {}
            finish_reason = choice.get("finish_reason")
            message_chunk = _convert_delta(delta, caps, finish_reason)
            usage = raw.get("usage")
            if usage and not message_chunk.content and not message_chunk.tool_calls:
                message_chunk.response_metadata["usage"] = usage
            yield message_chunk


# ---------------------------------------------------------------------------
# .env loading and provider env sync (unchanged surface)
# ---------------------------------------------------------------------------


def _redact_env_source(loaded: Path | None) -> str:
    if loaded is None:
        return "none (no .env file found)"
    for label, candidate in zip(_ENV_LABELS, _ENV_CANDIDATES):
        if loaded == candidate:
            return label
    return "<.env>"


def _redact_base_url_for_log(raw: str | None) -> str:
    if not raw or not raw.strip():
        return "(unset)"
    try:
        parsed = urlsplit(raw.strip())
    except ValueError:
        return "<base-url>"
    if not parsed.scheme or not parsed.hostname:
        return "<base-url>"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not_installed"


def _redact_env_flag(name: str) -> str:
    value = os.getenv(name, "")
    return "set" if value else "unset"


def _redact_proxy_url(name: str, raw: str | None) -> str:
    if not raw:
        return "unset"
    if name.upper().endswith("NO_PROXY"):
        return "set"
    return _redact_base_url_for_log(raw)


def _deepseek_adapter_mode() -> str:
    mode = os.getenv("VIBE_TRADING_DEEPSEEK_ADAPTER", "auto").strip().lower()
    aliases = {
        "compat": "openai-compatible",
        "compatible": "openai-compatible",
        "openai": "openai-compatible",
        "openai_compatible": "openai-compatible",
    }
    return aliases.get(mode, mode or "auto")


def _load_env_file(path: Path) -> None:
    if load_dotenv is not None:
        load_dotenv(dotenv_path=path, override=False)
    else:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _ensure_dotenv() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    loaded = None
    for candidate in _ENV_CANDIDATES:
        if candidate.exists():
            _load_env_file(candidate)
            loaded = candidate
            break
    _dotenv_loaded = True
    logger.info(
        "dotenv resolved from %s | provider=%s model=%s base=%s",
        _redact_env_source(loaded),
        os.getenv("LANGCHAIN_PROVIDER", "(unset)"),
        os.getenv("LANGCHAIN_MODEL_NAME", "(unset)"),
        _redact_base_url_for_log(os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")),
    )


def _normalize_ollama_base_url(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _sync_provider_env() -> None:
    """Map provider-specific env vars to OPENAI_* for the OpenAI SDK client."""
    _ensure_dotenv()
    provider = os.getenv("LANGCHAIN_PROVIDER", "openai").lower()

    if provider in {"openai-codex", "openai_codex"}:
        codex_url = os.getenv("OPENAI_CODEX_BASE_URL", "https://chatgpt.com/backend-api/codex/responses")
        os.environ["OPENAI_API_BASE"] = codex_url
        os.environ["OPENAI_BASE_URL"] = codex_url
        os.environ.pop("OPENAI_API_KEY", None)
        return

    key_env, base_env = provider_env_names(provider, os.getenv("LANGCHAIN_MODEL_NAME", ""))

    if key_env is not None:
        api_key = os.getenv(key_env, "") or os.getenv("OPENAI_API_KEY", "")
    else:
        api_key = os.getenv("OPENAI_API_KEY", "") or "ollama"

    base_url = os.getenv(base_env, "") or os.getenv("OPENAI_BASE_URL", "") or os.getenv("OPENAI_API_BASE", "")
    if provider == "ollama" and base_url:
        base_url = _normalize_ollama_base_url(base_url)

    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_API_BASE"] = base_url
        os.environ.setdefault("OPENAI_BASE_URL", base_url)


def provider_diagnostics() -> dict[str, Any]:
    _sync_provider_env()
    provider = os.getenv("LANGCHAIN_PROVIDER", "openai").strip().lower()
    model = os.getenv("LANGCHAIN_MODEL_NAME", "").strip()
    caps = get_provider_capabilities(provider, model)
    key_env, base_env = provider_env_names(provider, model)
    base_url = os.getenv(base_env, "") or os.getenv("OPENAI_BASE_URL", "") or os.getenv("OPENAI_API_BASE", "")
    proxy_names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]
    package_names = ["openai", "langchain-deepseek"]
    native_package_version = (
        _package_version(caps.native_adapter_package)
        if caps.native_adapter_package
        else None
    )
    adapter_mode = _deepseek_adapter_mode() if caps.name == "deepseek" else "openai-compatible"
    adapter_type = (
        "native"
        if caps.name == "deepseek"
        and adapter_mode != "openai-compatible"
        and native_package_version not in {None, "not_installed"}
        else "openai-compatible"
    )
    return {
        "provider": caps.name if provider in {"kimi", "openai_codex"} else provider,
        "model": model,
        "base_url": _redact_base_url_for_log(base_url),
        "api_key": {key_env: _redact_env_flag(key_env)} if key_env else {},
        "env": {
            "LANGCHAIN_PROVIDER": _redact_env_flag("LANGCHAIN_PROVIDER"),
            "LANGCHAIN_MODEL_NAME": _redact_env_flag("LANGCHAIN_MODEL_NAME"),
            "OPENAI_API_KEY": _redact_env_flag("OPENAI_API_KEY"),
            "OPENAI_BASE_URL": _redact_base_url_for_log(os.getenv("OPENAI_BASE_URL")),
            "OPENAI_API_BASE": _redact_base_url_for_log(os.getenv("OPENAI_API_BASE")),
        },
        "proxy": {
            name: _redact_proxy_url(name, os.getenv(name))
            for name in proxy_names
            if os.getenv(name)
        },
        "packages": {name: _package_version(name) for name in package_names},
        "timeout_seconds": int(os.getenv("TIMEOUT_SECONDS", "120")),
        "max_retries": int(os.getenv("MAX_RETRIES", "2")),
        "reasoning_effort": os.getenv("LANGCHAIN_REASONING_EFFORT", "").strip().lower(),
        "adapter": {
            "type": adapter_type,
            "mode": adapter_mode,
            "native_package": caps.native_adapter_package,
            "native_package_version": native_package_version,
        },
        "capabilities": {
            "capture_reasoning": caps.capture_reasoning,
            "send_reasoning_content": caps.send_reasoning_content,
            "gemini_thought_signatures": caps.gemini_thought_signatures,
            "openrouter_reasoning_body": caps.openrouter_reasoning_body,
        },
    }


# ---------------------------------------------------------------------------
# DeepSeek native adapter (optional; langchain-deepseek may still be installed
# but we no longer depend on it as the default path)
# ---------------------------------------------------------------------------


def _build_native_deepseek(
    *,
    model: str,
    temperature: float,
    callbacks: Any = None,
) -> Any | None:
    """Return a native DeepSeek adapter, if the optional package is installed.

    With langchain removed we prefer the openai-compatible path; this shim is
    retained so configurations that explicitly opted into the native adapter
    still work if the package is independently installed. It returns a
    ``NativeLLM`` pointed at the DeepSeek OpenAI-compatible endpoint, which is
    functionally equivalent for our usage.
    """
    try:
        import_module("langchain_deepseek")  # only used to detect presence
    except Exception as exc:  # noqa: BLE001 - optional adapter fallback
        logger.info("DeepSeek native adapter unavailable; using OpenAI-compatible path: %s", exc)
        return None

    key_env, base_env = provider_env_names("deepseek", model)
    api_key = os.getenv(key_env or "", "") or os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv(base_env, "") or os.getenv("OPENAI_BASE_URL", "") or os.getenv("OPENAI_API_BASE", "")
    return NativeLLM(
        model=model,
        temperature=temperature,
        timeout=int(os.getenv("TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("MAX_RETRIES", "2")),
        base_url=base_url,
        api_key=api_key,
        vibe_provider="deepseek",
    )


def build_llm(*, model_name: Optional[str] = None, callbacks: Any = None) -> Any:
    """Construct a NativeLLM (or OpenAICodexLLM) instance.

    Args:
        model_name: Model name; defaults to LANGCHAIN_MODEL_NAME.
        callbacks: Kept for backward-compat; ignored by NativeLLM.

    Returns:
        A NativeLLM instance (or OpenAICodexLLM for the codex provider).

    Raises:
        RuntimeError: If the openai SDK is missing or LANGCHAIN_MODEL_NAME is unset.
    """
    _sync_provider_env()
    name = model_name or os.getenv("LANGCHAIN_MODEL_NAME", "").strip()
    if not name:
        raise RuntimeError("LANGCHAIN_MODEL_NAME is not set")
    temperature = float(os.getenv("LANGCHAIN_TEMPERATURE", "0.0"))
    provider = os.getenv("LANGCHAIN_PROVIDER", "openai").lower()
    caps = get_provider_capabilities(provider, name)

    if provider in {"openai-codex", "openai_codex"}:
        from src.providers.openai_codex import OpenAICodexLLM

        effort = os.getenv("LANGCHAIN_REASONING_EFFORT", "").strip().lower()
        return OpenAICodexLLM(
            model=name,
            temperature=temperature,
            timeout=int(os.getenv("TIMEOUT_SECONDS", "120")),
            reasoning_effort=effort or None,
        )

    if provider == "deepseek":
        adapter_mode = _deepseek_adapter_mode()
        if adapter_mode != "openai-compatible":
            native_llm = _build_native_deepseek(
                model=name,
                temperature=temperature,
                callbacks=callbacks,
            )
            if native_llm is not None:
                return native_llm
            if adapter_mode == "native":
                raise RuntimeError(
                    "VIBE_TRADING_DEEPSEEK_ADAPTER=native requires langchain-deepseek"
                )

    if OpenAI is None:
        raise RuntimeError("openai SDK is not installed")

    # MiniMax requires temperature in (0.0, 1.0] — clamp to 0.01 when the
    # default 0.0 is used to avoid an API validation error.
    if provider == "minimax" and temperature <= 0.0:
        temperature = 0.01
    # Moonshot kimi-k2.x reasoning models reject any temperature other than 1.
    if caps.name == "moonshot" and name.lower().startswith("kimi-k2") and temperature != 1.0:
        logger.info("Forcing temperature=1.0 for %s (provider requirement)", name)
        temperature = 1.0
    # Optional reasoning activation for relays requiring opt-in (e.g. OpenRouter).
    effort = os.getenv("LANGCHAIN_REASONING_EFFORT", "").strip().lower()
    extra_body = {"reasoning": {"effort": effort}} if effort and caps.openrouter_reasoning_body else None

    key_env, base_env = provider_env_names(provider, name)
    api_key = os.getenv(key_env or "", "") or os.getenv("OPENAI_API_KEY", "")
    if provider == "ollama" and not api_key:
        api_key = "ollama"
    base_url = (
        os.getenv(base_env, "")
        or os.getenv("OPENAI_BASE_URL", "")
        or os.getenv("OPENAI_API_BASE", "")
    )
    if provider == "ollama" and base_url:
        base_url = _normalize_ollama_base_url(base_url)

    build_kwargs: dict[str, Any] = {
        "model": name,
        "temperature": temperature,
        "timeout": int(os.getenv("TIMEOUT_SECONDS", "120")),
        "max_retries": int(os.getenv("MAX_RETRIES", "2")),
        "base_url": base_url,
        "api_key": api_key,
        "vibe_provider": provider,
        "extra_body": extra_body,
    }
    # Only pass default_headers when non-empty so fakes/tests asserting the
    # absence of that key (e.g. ``"default_headers" not in captured``) hold.
    if caps.default_headers:
        build_kwargs["default_headers"] = dict(caps.default_headers)
    return _resolve_llm_cls()(**build_kwargs)


# Backwards-compatibility shim: tests and external code historically imported
# ``ChatOpenAIWithReasoning``. Expose NativeLLM under that name so monkey-patches
# (patch.object(llm_mod, "ChatOpenAIWithReasoning", _FakeChatOpenAI)) keep working
# without a find/replace across the test suite.
ChatOpenAIWithReasoning = NativeLLM


def _resolve_llm_cls() -> type:
    """Look up the active LLM class through module globals.

    Tests monkey-patch ``ChatOpenAIWithReasoning`` on this module; reading it
    via globals() here makes that patch effective inside ``build_llm`` without
    requiring callers to change.
    """
    import sys
    return globals().get("ChatOpenAIWithReasoning", NativeLLM)
