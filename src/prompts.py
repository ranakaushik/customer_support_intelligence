"""Loads versioned prompt files from prompts/.

Prompts live in files (not inline strings) so every version is kept, diffable, and can be
referenced from docs/prompt_engineering.md next to the numbers it produced.

File format (prompts/<step>_<version>.md):
    any notes for humans...
    === SYSTEM ===
    system instruction text
    === USER ===
    user message template with {{placeholders}}
"""

import re

from src import config

_SYSTEM = "=== SYSTEM ==="
_USER = "=== USER ==="


def load_prompt(step: str, version: str | None = None) -> tuple[str, str, str]:
    """Returns (system, user_template, version_label) for a step, e.g. ("...", "...", "classify_v1")."""
    version = version or config.PROMPT_VERSIONS[step]
    path = config.PROMPTS_DIR / f"{step}_{version}.md"
    text = path.read_text(encoding="utf-8")
    if _SYSTEM not in text or _USER not in text:
        raise ValueError(f"{path.name} must contain '{_SYSTEM}' and '{_USER}' sections")
    system = text.split(_SYSTEM, 1)[1].split(_USER, 1)[0].strip()
    user = text.split(_USER, 1)[1].strip()
    return system, user, f"{step}_{version}"


_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def fill(template: str, **values: str) -> str:
    # {{name}} placeholders instead of str.format: prompts contain JSON braces and "$" amounts
    # that str.format / string.Template would misinterpret.
    missing = set(_PLACEHOLDER.findall(template)) - values.keys()
    if missing:
        raise ValueError(f"Unfilled placeholders: {sorted(missing)}")
    # Single pass, so a customer message that itself contains "{{policies}}" is never expanded.
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)
