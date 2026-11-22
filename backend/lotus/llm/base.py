"""LLM abstraction: every call returns a schema-validated Pydantic object.

Agents never parse free text. Each call names a `task`, gives a system and user
prompt, an output schema, and a structured `context` (used by the offline mock
and recorded in the trace). A global semaphore provides back-pressure so
parallel fan-out cannot exceed the provider's token/concurrency budget.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import settings
from ..observability import tracer

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM:
    name = "base"

    def __init__(self, max_concurrency: int | None = None):
        self._sem = asyncio.Semaphore(max_concurrency or settings.llm_max_concurrency)

    async def _complete(self, system: str, user: str, schema: dict[str, Any], task: str,
                        context: dict[str, Any]) -> str:
        raise NotImplementedError

    async def structured(self, task: str, system: str, user: str, schema: Type[T],
                         context: dict[str, Any] | None = None) -> T:
        context = context or {}
        json_schema = schema.model_json_schema()
        async with self._sem:
            async with tracer.span(f"llm.{task}", kind="llm", provider=self.name) as span:
                last_err: Exception | None = None
                for attempt in range(2):
                    raw = await self._complete(system, user, json_schema, task, context)
                    try:
                        out = schema.model_validate(json.loads(_strip_fences(raw)))
                        span["attrs"]["attempts"] = attempt + 1
                        return out
                    except (ValidationError, json.JSONDecodeError) as exc:
                        last_err = exc
                        user = (f"{user}\n\nYour previous reply was not valid JSON for the schema "
                                f"({type(exc).__name__}). Reply with JSON only.")
                raise LLMError(f"{task}: invalid structured output: {last_err}")


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def json_instructions(schema: dict[str, Any]) -> str:
    return ("Respond with a single JSON object that validates against this JSON Schema. "
            "No prose, no code fences.\n" + json.dumps(schema))
