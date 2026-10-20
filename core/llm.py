"""LLM client for checks that need judgment (spelling, topic). Any OpenAI-compatible chat endpoint.

Configured from the environment: PROFESSIONALIZE_API_KEY, PROFESSIONALIZE_BASE_URL, PROFESSIONALIZE_LLM_MODEL
(the same names as the other internal tools that use this endpoint). Never print or store the key.

The endpoint is shared with other teams: at most `concurrency` calls run at once, and after a failed call (rate
limit, timeout, server error) every call waits `backoff` seconds before the next attempt.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass

ENV_KEY, ENV_BASE_URL, ENV_MODEL = "PROFESSIONALIZE_API_KEY", "PROFESSIONALIZE_BASE_URL", "PROFESSIONALIZE_LLM_MODEL"
DEFAULT_CONCURRENCY = 4
DEFAULT_BACKOFF = 30.0  # seconds all calls pause after a failure


class LLMError(RuntimeError):
    """Short, secret-free description of a failed call."""


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_dict(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens}


class LLM:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 180.0,
                 concurrency: int = DEFAULT_CONCURRENCY, backoff: float = DEFAULT_BACKOFF):
        from openai import OpenAI  # lazy: offline tests use a fake

        self.model, self.concurrency, self.backoff = model, concurrency, backoff
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)
        self.usage = Usage()
        self._slots = threading.Semaphore(concurrency)
        self._pause_until = 0.0

    @classmethod
    def from_env(cls, **kw) -> LLM:
        missing = [k for k in (ENV_KEY, ENV_BASE_URL, ENV_MODEL) if not os.environ.get(k)]
        if missing:
            raise LLMError(f"not configured: set {', '.join(missing)} in .env")
        return cls(os.environ[ENV_KEY], os.environ[ENV_BASE_URL], os.environ[ENV_MODEL], **kw)

    def complete(self, system: str, user: str) -> str:
        with self._slots:
            wait = self._pause_until - time.monotonic()
            if wait > 0:  # an earlier call failed: give the shared endpoint a break
                time.sleep(wait)
            try:
                r = self.client.chat.completions.create(
                    model=self.model, temperature=0,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            except Exception as e:  # noqa: BLE001 - the SDK raises many types; keep it short and secret-free
                self._pause_until = time.monotonic() + self.backoff
                raise LLMError(f"{type(e).__name__}: {str(e)[:160]}") from None
        self.usage.calls += 1
        if r.usage:
            self.usage.prompt_tokens += r.usage.prompt_tokens or 0
            self.usage.completion_tokens += r.usage.completion_tokens or 0
        return (r.choices[0].message.content or "").strip()


def parse_json(text: str) -> dict:
    """The JSON object in a reply (models sometimes wrap it in prose or a code fence)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError("reply has no JSON object")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"reply is not valid JSON ({e.msg})") from None
