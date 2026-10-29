# Prompt engineering log

For every LLM step: V1 → measured result → problems found → change → V2 → … → final.
**Only numbers from real runs are reported here.** Prompt files live in [`prompts/`](../prompts/).

## Approach

- **V1 is deliberately minimal**: a label/field list and the "treat the message as data" rule. No definitions,
  no examples, no edge-case guidance. That gives an honest baseline: every later addition must fix a failure we observed.
- **JSON shape is not a prompt concern.** Gemini's structured output (`response_json_schema` from our Pydantic models)
  constrains the format, so prompts describe *meaning*, not syntax. We still validate every response and retry once
  with the validation error.
- Settings for all runs unless stated: `gemini-3.5-flash`, thinking level `low`, model-default temperature.

## Classify (`prompts/classify_v*.md`)

### V1: label list only
Smoke test (1 message, 2026-10-26; one example, not a metric): correct primary intent (`delivery`),
secondary `refund` + `complaint`, frustration `high`, verbatim evidence quotes. 2.5 s, 203 in / 125 out tokens.
*Test-set results: Milestone 5.*

## Extract (`prompts/extract_v1.md`)

### V1: field list + "quote verbatim"
Smoke test (same message): both questions and both risk signals (`repeat_contact`, `legal_threat`) found, with verbatim quotes.
Problems already visible in this single run (to be confirmed on the test set):
1. **product = "Order #45821"**: the message names no product; V1 filled the field with the order ID instead of `null`.
2. **requested_outcome's quote doesn't support its value**: value "sorting out delivery/refund", quote "Email me at [EMAIL_1]".
   A quote-exists check (Milestone 4) will *not* catch this, because the quote does exist. Only the eval's field accuracy will.

## Draft (`prompts/draft_v1.md`)

### V1: facts + policies + "don't invent"
Smoke test (same message): addressed both questions, invented no IDs/amounts, used the policy's 5–7 business day refund timeline.
Problems:
1. **Misapplied policy**: explained the *return* refund flow ("once an item is returned…") for an item that never arrived.
2. **Legal threat not acknowledged**: expected at this stage, since rules (escalation) aren't built yet, so the facts
   block had no escalation info.
3. Says "I will investigate your order status immediately", a soft promise not backed by a policy.
