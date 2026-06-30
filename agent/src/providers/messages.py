"""Duck-typed AIMessage types for the native OpenAI-compatible LLM client.

These replace the langchain_core.messages.{AIMessage, AIMessageChunk,
HumanMessage, SystemMessage} surface that the rest of the codebase used to
reach through ChatOpenAI. They expose exactly the attributes the ChatLLM
parser and the agent loop read:

    content              str | None
    tool_calls           list[dict]  ({"id","name","args", ...})
    additional_kwargs    dict         (reasoning_content, signatures, ...)
    response_metadata    dict         (finish_reason, model_name, usage, ...)
    usage_metadata       dict | None  ({"input_tokens","output_tokens","total_tokens"})
    type                 str          ("ai" | "human" | "system" — used by
                          ChatOpenAIWithReasoning thought-signature path)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _merge_dicts(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge two dicts; for list values under known keys, concatenate."""
    merged: Dict[str, Any] = dict(a)
    for key, value in b.items():
        if (
            key in merged
            and isinstance(merged[key], list)
            and isinstance(value, list)
        ):
            merged[key] = merged[key] + value
        elif (
            key in merged
            and isinstance(merged[key], str)
            and isinstance(value, str)
        ):
            merged[key] = merged[key] + value
        else:
            merged[key] = value
    return merged


@dataclass
class AIMessage:
    """Non-streaming assistant message (duck-typed langchain replacement)."""

    content: Any = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    additional_kwargs: Dict[str, Any] = field(default_factory=dict)
    response_metadata: Dict[str, Any] = field(default_factory=dict)
    type: str = "ai"

    @property
    def usage_metadata(self) -> Optional[Dict[str, int]]:
        usage = self.response_metadata.get("usage") or self.additional_kwargs.get("usage_metadata")
        if usage is None:
            return None
        if isinstance(usage, dict):
            normalized: Dict[str, int] = {}
            for src_key, dst_key in (
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
                ("total_tokens", "total_tokens"),
                ("input_tokens", "input_tokens"),
                ("output_tokens", "output_tokens"),
            ):
                value = usage.get(src_key)
                if isinstance(value, int):
                    normalized[dst_key] = value
            if "total_tokens" not in normalized and "input_tokens" in normalized and "output_tokens" in normalized:
                normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]
            return normalized or None
        return None

    def __add__(self, other: "AIMessage") -> "AIMessage":
        return _accumulate(self, other)


@dataclass
class AIMessageChunk(AIMessage):
    """Streaming chunk. ``__add__`` accumulates content / tool_calls / reasoning."""

    type: str = "AIMessageChunk"


@dataclass
class HumanMessage:
    content: Any = ""
    additional_kwargs: Dict[str, Any] = field(default_factory=dict)
    type: str = "human"


@dataclass
class SystemMessage:
    content: Any = ""
    additional_kwargs: Dict[str, Any] = field(default_factory=dict)
    type: str = "system"


def _accumulate(left: AIMessage, right: AIMessage) -> AIMessage:
    """Merge two AIMessages following the semantics langchain provided.

    - content strings concatenate.
    - tool_calls lists concatenate (streaming tool-call assembly).
    - additional_kwargs merge dict-wise; reasoning_content / reasoning
      concatenate; tool_call_thought_signatures concatenate.
    - response_metadata: finish_reason from the right wins (later chunk),
      usage merges.
    """
    left_content = left.content or ""
    right_content = right.content or ""
    if isinstance(left_content, str) and isinstance(right_content, str):
        content: Any = left_content + right_content
    else:
        content = right_content if left_content == "" else left_content

    merged_kwargs = _merge_dicts(left.additional_kwargs, right.additional_kwargs)

    # Explicit string concat for reasoning (in case a provider emits it as
    # a non-string type we still fall through to the merge above).
    for reasoning_key in ("reasoning_content", "reasoning"):
        lval = left.additional_kwargs.get(reasoning_key)
        rval = right.additional_kwargs.get(reasoning_key)
        if isinstance(lval, str) and isinstance(rval, str):
            merged_kwargs[reasoning_key] = lval + rval

    merged_tool_calls = [*left.tool_calls, *right.tool_calls]

    left_meta = left.response_metadata or {}
    right_meta = right.response_metadata or {}
    finish_reason = right_meta.get("finish_reason") or left_meta.get("finish_reason", "stop")
    usage = _merge_dicts(left_meta.get("usage") or {}, right_meta.get("usage") or {})
    response_metadata = {"finish_reason": finish_reason}
    if usage:
        response_metadata["usage"] = usage
    for key in ("model_name", "model"):
        if right_meta.get(key):
            response_metadata[key] = right_meta[key]
        elif left_meta.get(key):
            response_metadata[key] = left_meta[key]

    cls = AIMessageChunk if isinstance(right, AIMessageChunk) or isinstance(left, AIMessageChunk) else AIMessage
    return cls(
        content=content,
        tool_calls=merged_tool_calls,
        additional_kwargs=merged_kwargs,
        response_metadata=response_metadata,
    )
