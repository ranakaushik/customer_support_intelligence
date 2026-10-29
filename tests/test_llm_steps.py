"""Tests for the LLM plumbing (client retry logic, prompt loading, pipeline wiring).

No API key or network needed: a fake stands in for Gemini. These tests check OUR code
(retry, validation, masking, step order), not the model's answers. Model quality is measured
by the evaluation in eval/.
"""

import json
from types import SimpleNamespace

import pytest

from src import llm_client
from src.llm_client import GeminiClient, LLMResponse
from src.pipeline import run_pipeline
from src.prompts import fill, load_prompt
from src.schemas import ClassificationResult, DraftResult, ExtractionResult

VALID_CLASSIFICATION = {
    "primary_intent": "delivery", "secondary_intents": ["refund"], "intent_evidence": ["never arrived"],
    "sentiment": "negative", "frustration": "high", "is_sarcastic": False, "language": "en",
    "is_mixed_language": False, "confidence": 0.9, "reasoning": "Late delivery, wants refund.",
}


# --- prompts ---------------------------------------------------------------

@pytest.mark.parametrize("step", ["classify", "extract", "draft"])
def test_v1_prompts_load(step):
    system, user, version = load_prompt(step, "v1")
    assert system and "{{message}}" in user
    assert version == f"{step}_v1"


def test_fill_rejects_missing_placeholder():
    with pytest.raises(ValueError):
        fill("{{facts}} {{message}}", message="hi")


def test_fill_does_not_expand_placeholders_inside_customer_text():
    # A message containing "{{policies}}" must stay literal, not pull in other prompt content.
    assert fill("{{message}} | {{policies}}", message="{{policies}}", policies="P") == "{{policies}} | P"


# --- GeminiClient retry behaviour (SDK replaced by a fake) -----------------

class FakeModels:
    """Returns the queued outputs one per call; an Exception in the queue is raised instead."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5)
        return SimpleNamespace(text=out, usage_metadata=usage)


def make_client(monkeypatch, outputs) -> tuple[GeminiClient, FakeModels]:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = GeminiClient(model="fake-model")
    fake = FakeModels(outputs)
    client._client = SimpleNamespace(models=fake)
    return client, fake


def test_valid_output_first_try(monkeypatch):
    client, fake = make_client(monkeypatch, [json.dumps(VALID_CLASSIFICATION)])
    resp = client.generate("sys", "user", ClassificationResult)
    assert resp.parsed.primary_intent.value == "delivery"
    assert resp.attempts == 1 and resp.error is None
    assert resp.input_tokens == 100 and resp.output_tokens == 25   # thinking tokens counted as output


def test_invalid_output_is_retried_once_with_the_error(monkeypatch):
    bad = dict(VALID_CLASSIFICATION, primary_intent="refunds")        # not in the enum
    client, fake = make_client(monkeypatch, [json.dumps(bad), json.dumps(VALID_CLASSIFICATION)])
    resp = client.generate("sys", "user", ClassificationResult)
    assert resp.parsed is not None and resp.attempts == 2 and resp.error is None
    retry_turn = fake.calls[1][-1]["parts"][0]["text"]
    assert "failed validation" in retry_turn and "primary_intent" in retry_turn


def test_gives_up_after_second_invalid_output(monkeypatch):
    client, _ = make_client(monkeypatch, ["not json", "still not json"])
    resp = client.generate("sys", "user", ClassificationResult)
    assert resp.parsed is None and resp.attempts == 2
    assert resp.error.startswith("Invalid output")


def test_quota_error_is_not_retried(monkeypatch):
    err = llm_client.genai_errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    client, fake = make_client(monkeypatch, [err])
    resp = client.generate("sys", "user", ClassificationResult)
    assert resp.parsed is None and len(fake.calls) == 1
    assert "429" in resp.error


def test_server_error_is_retried_once(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    err = llm_client.genai_errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})
    client, fake = make_client(monkeypatch, [err, json.dumps(VALID_CLASSIFICATION)])
    resp = client.generate("sys", "user", ClassificationResult)
    assert resp.parsed is not None and len(fake.calls) == 2


# --- pipeline wiring with a fake LLMClient ---------------------------------

class FakeLLM:
    """Stands in for GeminiClient at the pipeline level: one canned answer per schema."""
    model = "fake"

    def __init__(self, draft_text="Hi, sorry about order 45821. We will email [EMAIL_1]."):
        self.prompts = []
        self.answers = {
            ClassificationResult: ClassificationResult(**VALID_CLASSIFICATION),
            ExtractionResult: ExtractionResult(),
            DraftResult: DraftResult(response=draft_text),
        }

    def generate(self, system, user, schema):
        self.prompts.append(user)
        return LLMResponse(parsed=self.answers[schema], attempts=1)


def test_pipeline_runs_all_steps_and_llm_never_sees_pii():
    fake = FakeLLM()
    result = run_pipeline("Order #45821 never arrived. Mail me at x@y.com, card 4111 1111 1111 1111", fake)
    assert [t.name for t in result.traces] == ["0 Preprocess", "1 Classify", "2 Extract", "5 Draft"]
    for prompt in fake.prompts:
        assert "x@y.com" not in prompt and "4111" not in prompt
    # ...but the customer's own email is restored in the reply shown to the agent.
    assert "x@y.com" in result.draft.response


def test_pipeline_skips_llm_for_garbage():
    fake = FakeLLM()
    result = run_pipeline("!!!!!!!!!!!!", fake)
    assert fake.prompts == []
    assert result.classification is None
