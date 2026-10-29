"""Runs the steps in order and records a trace for each one.

Milestone 2 state: Preprocess -> (Classify || Extract) -> Draft.
Rules (Step 4), verification (Steps 3/6) and final assembly (Step 7) are added in later milestones.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from src.classify import classify
from src.draft import draft
from src.extract import extract
from src.llm_client import GeminiClient, LLMClient
from src.preprocess import preprocess
from src.schemas import (ClassificationResult, Decision, DraftResult, ExtractionResult,
                         PreprocessResult, StepTrace)


class PipelineResult(BaseModel):
    preprocess: PreprocessResult
    classification: ClassificationResult | None = None
    extraction: ExtractionResult | None = None
    decision: Decision | None = None
    draft: DraftResult | None = None
    traces: list[StepTrace] = []
    # End-to-end wall-clock time. Not the sum of step latencies: Classify and Extract overlap.
    wall_ms: int = 0

    @property
    def total_tokens(self) -> tuple[int, int]:
        return sum(t.input_tokens for t in self.traces), sum(t.output_tokens for t in self.traces)


def unmask(text: str, pre: PreprocessResult) -> str:
    """Put the customer's own email/phone back into the reply. Card numbers are never restored."""
    for placeholder, value in pre.pii_map.items():
        text = text.replace(placeholder, "[card number removed]" if placeholder.startswith("[CARD_") else value)
    return text


def run_pipeline(message: str, client: LLMClient | None = None) -> PipelineResult:
    start = time.perf_counter()
    pre = preprocess(message)
    result = PipelineResult(preprocess=pre)
    result.traces.append(StepTrace(
        name="0 Preprocess", kind="code", latency_ms=int((time.perf_counter() - start) * 1000),
        output=pre.model_dump(mode="json", exclude={"original_text"}),
    ))
    if pre.should_skip_llm:
        # Empty or garbage input: nothing for an LLM to understand, so don't pay for calls.
        result.wall_ms = int((time.perf_counter() - start) * 1000)
        return result

    client = client or GeminiClient()

    # Classify and Extract both read only the preprocessed text, so they run at the same time.
    # Their latencies overlap; the trace still records each one's own duration.
    with ThreadPoolExecutor(max_workers=2) as pool:
        cls_future = pool.submit(classify, pre, client)
        ext_future = pool.submit(extract, pre, client)
        result.classification, cls_trace = cls_future.result()
        result.extraction, ext_trace = ext_future.result()
    result.traces += [cls_trace, ext_trace]

    if result.classification and result.extraction:
        result.draft, draft_trace = draft(pre, result.classification, result.extraction, result.decision, client)
        if result.draft:
            result.draft.response = unmask(result.draft.response, pre)
        result.traces.append(draft_trace)
    result.wall_ms = int((time.perf_counter() - start) * 1000)
    return result
