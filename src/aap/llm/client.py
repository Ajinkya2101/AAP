"""LLM client: ask(task, content, schema) -> JSON that matches the schema.

R-01: the LLM never receives tool definitions. R-02: every answer is JSON
constrained by a schema (vLLM structured output, xgrammar). For choices the
schema is an enum of candidates prepared by code.

LLM_BACKEND=vllm  -> OpenAI-compatible vLLM endpoint
LLM_BACKEND=stub  -> aap.llm.stub (ground-truth sidecars / first candidate),
                     so stages 5-11 and the agents run without a GPU.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

from aap.config import Settings, get_settings
from aap.logging import get_logger

log = get_logger(__name__)


class LlmError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True, raw: str | None = None):
        super().__init__(message)
        self.retryable = retryable   # False: the same request would fail the same way (job retries are pointless)
        self.raw = raw               # the model's output, kept for diagnosis (extraction_run.raw_output)


@dataclass
class LlmRequest:
    task: str                         # extract | choose
    system: str
    content: list[dict[str, Any]]     # OpenAI content parts (text / image_url)
    schema: dict[str, Any]
    model: str | None = None
    max_tokens: int = 4096
    # Not sent to the model. Lets the stub answer (sha256, page texts, candidates ...).
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class LlmResponse:
    data: dict[str, Any]
    raw: str
    model: str
    backend: str
    duration_ms: int
    usage: dict[str, Any] | None = None


class LlmClient(Protocol):
    backend: str

    def ask(self, req: LlmRequest) -> LlmResponse: ...


def _validate(schema: dict[str, Any], data: Any) -> None:
    """Light structural check (vLLM already enforces the schema; the stub might not)."""
    if schema.get("type") == "object":
        if not isinstance(data, dict):
            raise LlmError("LLM output is not a JSON object")
        missing = [k for k in schema.get("required", []) if k not in data]
        if missing:
            raise LlmError(f"LLM output misses required keys: {missing}")
        for key, sub in schema.get("properties", {}).items():
            if key in data and "enum" in sub and data[key] not in sub["enum"]:
                raise LlmError(f"LLM output {key}={data[key]!r} is not one of the allowed values")


class VllmClient:
    backend = "vllm"

    def __init__(self, settings: Settings):
        from openai import OpenAI

        self.settings = settings
        self._client = OpenAI(base_url=settings.vllm_base_url, api_key=settings.vllm_api_key,
                              timeout=settings.llm_timeout_seconds, max_retries=0)

    def ask(self, req: LlmRequest) -> LlmResponse:
        model = req.model or (self.settings.extract_model if req.task.startswith("extract")
                              else self.settings.choice_model)
        messages = [{"role": "system", "content": req.system}, {"role": "user", "content": req.content}]
        last_exc: Exception | None = None
        for attempt in range(2):   # ARCHITECTURE 11: one retry if the output does not parse
            t0 = time.monotonic()
            resp = self._client.chat.completions.create(
                model=model, temperature=self.settings.llm_temperature, max_tokens=req.max_tokens, messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_schema",
                                 "json_schema": {"name": req.task, "schema": req.schema, "strict": True}},
            )
            raw = resp.choices[0].message.content or ""
            finish = resp.choices[0].finish_reason
            duration = int((time.monotonic() - t0) * 1000)
            try:
                data = json.loads(raw)
                _validate(req.schema, data)
            except (json.JSONDecodeError, LlmError) as exc:
                last_exc = exc
                log.warning("llm.invalid_output", task=req.task, attempt=attempt, finish_reason=finish,
                            chars=len(raw), error=str(exc))
                # A truncated answer is usually a repetition loop on a poor scan; at the default
                # temperature 0 it would come back identical, so do not spend a second generation on it.
                if finish == "length":
                    raise LlmError(f"LLM output truncated at max_tokens={req.max_tokens} "
                                   f"({len(raw)} chars): {exc}", retryable=False, raw=raw) from exc
                continue
            usage = resp.usage.model_dump() if resp.usage else None
            return LlmResponse(data=data, raw=raw, model=model, backend=self.backend, duration_ms=duration,
                               usage=usage)
        raise LlmError(f"LLM output did not match the schema after retry: {last_exc}", raw=raw)

    def health(self) -> bool:
        try:
            self._client.models.list()
            return True
        except Exception:  # noqa: BLE001
            return False


@lru_cache
def get_llm() -> LlmClient:
    settings = get_settings()
    if settings.llm_backend == "vllm":
        return VllmClient(settings)
    from .stub import StubClient

    return StubClient(settings)
