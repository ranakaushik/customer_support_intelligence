"""Step 1 - Classify (LLM).

Why an LLM: intent, sarcasm and frustration depend on meaning, not keywords.
"Great, ANOTHER broken charger, love it" contains no negative keyword but is a complaint.
"""

from src.llm_client import LLMClient, to_trace
from src.prompts import fill, load_prompt
from src.schemas import ClassificationResult, PreprocessResult, StepTrace


def classify(pre: PreprocessResult, client: LLMClient) -> tuple[ClassificationResult | None, StepTrace]:
    system, user, version = load_prompt("classify")
    # The LLM only ever sees the masked text (no card numbers, emails or phone numbers).
    resp = client.generate(system, fill(user, message=pre.masked_text), ClassificationResult)
    return resp.parsed, to_trace("1 Classify", version, resp)
