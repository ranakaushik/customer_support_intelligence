"""The only module that talks to Gemini.

Why a wrapper instead of calling the SDK in each step:
  * One place for timeouts, error handling, token counting and the "retry once with the error" rule.
  * Tests swap in a fake client (anything with a `generate` method), so they run without an API key.

Errors are returned (in LLMResponse.error), not raised: the pipeline must always produce a result,
falling back to "needs human review" when a step fails.
"""

import json
import time
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from src import config
from src.schemas import StepTrace


@dataclass
class LLMResponse:
    parsed: BaseModel | None      # validated Pydantic object, or None if the step failed
    raw_text: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0        # visible output + thinking tokens (both are billed as output)
    attempts: int = 0
    error: str | None = None


class LLMClient(Protocol):
    model: str

    def generate(self, system: str, user: str, schema: type[BaseModel]) -> LLMResponse: ...


# Server-side hiccups worth one more try. 429 is NOT here: on the free tier it usually means the
# daily quota is gone, and retrying just burns time (we saw the SDK retry silently for minutes).
_TRANSIENT_CODES = {500, 502, 503, 504}


class GeminiClient:
    def __init__(self, model: str | None = None):
        if not config.has_gemini_credentials():
            raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        self.model = model or config.GEMINI_MODEL
        self._client = genai.Client(http_options=types.HttpOptions(
            timeout=config.LLM_TIMEOUT_S * 1000,
            # Turn off the SDK's own retries so all retry behaviour is visible in this file.
            retry_options=types.HttpRetryOptions(attempts=1),
        ))

    def _config(self, system: str, schema: type[BaseModel]) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            # Structured output: Gemini is constrained to JSON matching our Pydantic schema.
            # We still validate afterwards, because "constrained" is not the same as "guaranteed".
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
            temperature=config.LLM_TEMPERATURE,
            thinking_config=(types.ThinkingConfig(thinking_level=config.GEMINI_THINKING_LEVEL)
                             if config.GEMINI_THINKING_LEVEL else None),
        )

    def generate(self, system: str, user: str, schema: type[BaseModel]) -> LLMResponse:
        result = LLMResponse(parsed=None)
        contents: list[dict] = [{"role": "user", "parts": [{"text": user}]}]
        start = time.perf_counter()
        server_retry_used = False

        while result.attempts < 2:
            result.attempts += 1
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=contents, config=self._config(system, schema))
            except genai_errors.APIError as e:
                if e.code in _TRANSIENT_CODES and not server_retry_used:
                    server_retry_used = True
                    result.attempts -= 1      # a server error doesn't count against the JSON retry
                    time.sleep(2)
                    continue
                result.error = _describe_api_error(e, self.model)
                break
            except OSError as e:          # network down, DNS, timeout
                result.error = f"Could not reach Gemini: {e}"
                break

            usage = response.usage_metadata
            if usage:
                result.input_tokens += usage.prompt_token_count or 0
                result.output_tokens += (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
            result.raw_text = response.text or ""

            try:
                result.parsed = schema.model_validate_json(result.raw_text)
                result.error = None
                break
            except (ValidationError, json.JSONDecodeError) as e:
                # Retry once, showing the model its own output and the exact validation error.
                # Precise errors ("primary_intent: 'refunds' is not a valid enum member") are
                # usually enough for the model to fix the output on the second try.
                result.error = f"Invalid output: {_short(str(e))}"
                contents += [
                    {"role": "model", "parts": [{"text": result.raw_text}]},
                    {"role": "user", "parts": [{"text":
                        f"Your previous output failed validation:\n{_short(str(e))}\n"
                        "Return the corrected JSON only."}]},
                ]

        result.latency_ms = int((time.perf_counter() - start) * 1000)
        return result


def to_trace(name: str, prompt_version: str, resp: LLMResponse) -> StepTrace:
    return StepTrace(
        name=name, kind="llm", prompt_version=prompt_version,
        latency_ms=resp.latency_ms, input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
        attempts=resp.attempts, error=resp.error,
        output=resp.parsed.model_dump(mode="json") if resp.parsed else None,
    )


def _short(text: str, limit: int = 600) -> str:
    return text if len(text) <= limit else text[:limit] + " ..."


def _describe_api_error(e: genai_errors.APIError, model: str) -> str:
    if e.code == 429:
        return f"Gemini quota/rate limit reached for '{model}' (429). Free tier is ~20 requests/model/day."
    if e.code in (401, 403) or "API key" in (e.message or ""):
        return "Gemini rejected the credentials. Check GEMINI_API_KEY."
    if e.code == 404:
        return f"Gemini model '{model}' not found. Set GEMINI_MODEL to an available model."
    return f"Gemini API error {e.code}: {_short(e.message or '', 200)}"
