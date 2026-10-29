# AI usage

How AI tools were used in building this project, and how we checked their output.

| Activity | Tool | What it did | How we verified |
|---|---|---|---|
| Design critique | Claude Code | Critiqued the original pipeline (rules-only decisions, PII placeholders, parallel steps, structured output, injection defence) | Team reviewed and approved each change before build |
| Coding: Milestone 1 | Claude Code | Wrote schemas, preprocessing, unit tests | 43 unit tests; one real bug found by the tests (order-ID fragment `INV-2024` reported alongside `INV-2024-001`) and fixed |
| Coding: Milestone 2 | Claude Code | Wrote Gemini client (structured output, retry, error handling), V1 prompts, step modules, pipeline, CLI, mock policies | 12 more unit tests with a fake Gemini client; one real end-to-end run whose output was read and its problems logged in prompt_engineering.md |
| Debugging | Claude Code | Diagnosed a 3-minute hang on the first Gemini call as the SDK silently retrying 429 quota errors; disabled SDK retries | Reproduced with a 30 s timeout; error text showed the free-tier 20/day limit |
| Documentation | Claude Code | Wrote the technical specification, architecture and data-flow diagrams (Mermaid), and a headless-Chrome build script for SVG/PNG/PDF | Rendered output inspected visually; first versions had a misleading "sent to Gemini" boundary box enclosing local steps, tangled edges and half-empty PDF pages, all fixed and re-checked |
