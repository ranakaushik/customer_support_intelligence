"""Step 2 - Extract (LLM).

Why an LLM: products, questions and requested outcomes are phrased freely
("can u tell me if the blue one fits a 13 inch laptop" is a question without a question mark).
Hard identifiers (order IDs, amounts, dates) are NOT asked for here: regex in Step 0 finds them
exactly, and asking the LLM too would only add a chance to mis-copy them.

Every value carries a verbatim quote, which Step 3 checks against the message to catch hallucinations.
"""

from src.llm_client import LLMClient, to_trace
from src.prompts import fill, load_prompt
from src.schemas import ExtractionResult, PreprocessResult, StepTrace


def extract(pre: PreprocessResult, client: LLMClient) -> tuple[ExtractionResult | None, StepTrace]:
    system, user, version = load_prompt("extract")
    resp = client.generate(system, fill(user, message=pre.masked_text), ExtractionResult)
    return resp.parsed, to_trace("2 Extract", version, resp)
