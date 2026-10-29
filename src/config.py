"""Central settings. Everything that might change between runs (model, limits) lives here, not in code."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Model name comes from the environment so we can swap models in the evaluation without code changes.
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.5-flash"
# Gemini 3 models "think" before answering. More thinking = better reasoning but slower and more
# (billed) output tokens. Empty = model default. Values: minimal, low, medium, high.
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")
# Empty = model default. Google recommends the default (1.0) for Gemini 3; we measure the effect
# on consistency in the evaluation instead of assuming temperature 0 is better.
LLM_TEMPERATURE = float(os.environ["LLM_TEMPERATURE"]) if os.getenv("LLM_TEMPERATURE") else None
LLM_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "60"))

# Which prompt version each LLM step uses. Overridable per run (e.g. PROMPT_CLASSIFY=v2) so the
# evaluation can compare versions on the same test set.
PROMPT_VERSIONS = {
    # "or" (not a getenv default) so an empty line like "PROMPT_CLASSIFY=" in .env also falls back.
    "classify": os.getenv("PROMPT_CLASSIFY") or "v1",
    "extract": os.getenv("PROMPT_EXTRACT") or "v1",
    "draft": os.getenv("PROMPT_DRAFT") or "v1",
}

PROMPTS_DIR = PROJECT_ROOT / "prompts"
RULES_PATH = PROJECT_ROOT / "src" / "rules.yaml"
POLICIES_PATH = PROJECT_ROOT / "data" / "policies.md"

# Preprocessing limits. Very long inputs are truncated (and flagged) to bound cost and latency.
MAX_INPUT_CHARS = 5000
# Below this many words a message is "very short": we still process it but flag it as likely vague.
SHORT_MESSAGE_WORDS = 4


def has_gemini_credentials() -> bool:
    # genai.Client() accepts either variable name.
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
