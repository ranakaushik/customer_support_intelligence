"""Data contracts between pipeline steps.

Every step reads and writes one of these models. Why Pydantic:
  * LLM output is untrusted text; validating it against a schema turns "looks like JSON" into
    "has exactly the fields and values we expect" (or a clear error we can retry with).
  * The same models generate the JSON schema we send to Gemini, so prompt and validator never drift apart.
"""

from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Fixed vocabularies. Enums (not free text) so rules can match on them reliably.
# ---------------------------------------------------------------------------

class Intent(str, Enum):
    REFUND = "refund"
    BILLING = "billing"
    TECHNICAL_ISSUE = "technical_issue"
    DELIVERY = "delivery"
    ACCOUNT_ACCESS = "account_access"
    PRODUCT_QUESTION = "product_question"
    COMPLAINT = "complaint"
    CANCELLATION = "cancellation"
    FEEDBACK = "feedback"
    # Off-topic or spam is a different handling path from "unclear" (ignore vs. ask a question),
    # so it gets its own label.
    SPAM_OFF_TOPIC = "spam_off_topic"
    UNCLEAR = "unclear"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class Frustration(str, Enum):
    # Ordered levels instead of a 0-1 float: easier for the LLM to apply consistently
    # and easier for rules to compare.
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Urgency(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


URGENCY_ORDER = [Urgency.LOW, Urgency.MEDIUM, Urgency.HIGH, Urgency.CRITICAL]


class SignalType(str, Enum):
    """Risk signals the Extract step looks for. Rules (not the LLM) decide what they mean."""
    LEGAL_THREAT = "legal_threat"              # lawyer, sue, consumer court, regulator
    SAFETY_ISSUE = "safety_issue"              # injury, fire, burning smell, health risk
    MANAGER_REQUEST = "manager_request"        # explicitly asks for a supervisor / manager
    REPEAT_CONTACT = "repeat_contact"          # "third time I'm writing", "called twice"
    CHURN_THREAT = "churn_threat"              # will cancel / switch to a competitor
    PUBLIC_EXPOSURE_THREAT = "public_exposure_threat"  # review sites, social media, press
    SECURITY_CONCERN = "security_concern"      # account hacked, unauthorized charge, data leak


# ---------------------------------------------------------------------------
# Step 0: preprocessing (code only)
# ---------------------------------------------------------------------------

class Amount(BaseModel):
    raw: str                      # exactly as written, e.g. "Rs. 4,999" -- used for evidence checks
    value: float                  # parsed number, used by the high-value-refund rule
    currency: str | None = None   # ISO-ish code (USD, INR, EUR, GBP) when we can tell


class Identifiers(BaseModel):
    """Hard identifiers found by regex. Regex is exact and free, so we don't ask the LLM for these."""
    order_ids: list[str] = Field(default_factory=list)
    amounts: list[Amount] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    has_card_number: bool = False   # we never keep the number itself


class PreprocessResult(BaseModel):
    original_text: str
    normalized_text: str
    # What the LLM sees: PII replaced by placeholders like [EMAIL_1]. Evidence quotes are checked
    # against this text because it is the text the model actually read.
    masked_text: str
    # placeholder -> original value. Kept in memory only (to un-mask the draft), never logged.
    pii_map: dict[str, str] = Field(default_factory=dict, exclude=True)
    identifiers: Identifiers
    word_count: int
    # Flags are plain strings so they can be shown directly in Reasoning / Evidence.
    flags: list[str] = Field(default_factory=list)
    # True means "don't call the LLM at all" (empty or garbage input).
    should_skip_llm: bool = False


# ---------------------------------------------------------------------------
# Steps 1-2: LLM outputs. Every claim carries the quote that supports it.
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    value: str
    quote: str = Field(description="Exact, verbatim substring of the customer message that supports the value.")


class ClassificationResult(BaseModel):
    primary_intent: Intent
    secondary_intents: list[Intent] = Field(default_factory=list)
    intent_evidence: list[str] = Field(
        default_factory=list,
        description="Verbatim quotes from the message that show the intent(s).",
    )
    sentiment: Sentiment
    frustration: Frustration
    is_sarcastic: bool = False
    language: str = Field(description="ISO 639-1 code of the main language, e.g. 'en', 'hi'.")
    is_mixed_language: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="One or two sentences explaining the classification.")


class Signal(BaseModel):
    type: SignalType
    quote: str


class ExtractionResult(BaseModel):
    product: Evidence | None = None
    issue_description: Evidence | None = None
    questions: list[Evidence] = Field(default_factory=list)
    requested_outcome: Evidence | None = None
    prior_contact: Evidence | None = None
    deadline: Evidence | None = None
    signals: list[Signal] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Steps 3-4: deterministic decision (rules)
# ---------------------------------------------------------------------------

class Decision(BaseModel):
    urgency: Urgency
    escalation: bool
    recommended_action: str
    needs_human_review: bool
    # Names of the rules that fired, e.g. "legal_threat -> Critical". This is the audit trail.
    fired_rules: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Step 5: draft (LLM)
# ---------------------------------------------------------------------------

class DraftResult(BaseModel):
    response: str
    is_clarifying_question: bool = False
    # Which extracted question each part of the reply addresses, so code can check none were skipped.
    addressed_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-step trace: what ran, how long, how many tokens. Feeds the UI, the CLI and the evaluation.
# ---------------------------------------------------------------------------

class StepTrace(BaseModel):
    name: str
    kind: str                         # "code" | "llm" | "rules"
    prompt_version: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0            # includes "thinking" tokens: Gemini bills them as output
    attempts: int = 0
    error: str | None = None
    output: dict | None = None


# ---------------------------------------------------------------------------
# Step 7: final output for the support team
# ---------------------------------------------------------------------------

class SupportResult(BaseModel):
    customer_intent: str
    urgency: Urgency
    key_information: dict[str, str | list[str]]
    recommended_action: str
    escalation: bool
    draft_response: str
    reasoning_evidence: list[str]
    needs_human_review: bool
    review_reasons: list[str] = Field(default_factory=list)
