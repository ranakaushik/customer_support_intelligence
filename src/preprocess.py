"""Step 0 - Preprocess (code only, no LLM).

Why code and not an LLM here: every task in this step has an exact, checkable answer
(is it empty? is this a valid card number? where are the order IDs?). Regex is free, instant,
deterministic and unit-testable, and an LLM would sometimes mis-copy an ID.

Order of operations matters:
  1. normalize text           -> every later step (and every evidence check) uses the same text
  2. reject empty / garbage   -> don't pay for LLM calls that can't produce anything useful
  3. mask PII                 -> the LLM never sees card numbers, emails or phone numbers
  4. extract identifiers      -> run on the masked text, so a phone number is never mistaken for an order ID
  5. flag injection attempts  -> later steps treat the message as data; rules route it to review
"""

import re
import unicodedata

from src import config
from src.schemas import Amount, Identifiers, PreprocessResult

# ---------------------------------------------------------------------------
# 1. Normalization
# ---------------------------------------------------------------------------

# Zero-width space and byte-order mark: invisible, but they break substring checks on evidence quotes.
_INVISIBLE = re.compile("[​﻿]")


def normalize(text: str) -> str:
    # NFKC folds look-alike characters (full-width digits, non-breaking spaces) into plain ones,
    # so "１２３" and "123" are matched by the same regex.
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    # Keep paragraph breaks (they help the LLM read long messages) but at most one blank line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 2. Empty / garbage detection
# ---------------------------------------------------------------------------

def _is_letter(ch: str) -> bool:
    # Unicode categories L* (letters) and M* (combining marks). Marks matter for scripts like
    # Devanagari, where vowel signs are not "letters" but are clearly real text.
    return unicodedata.category(ch)[0] in ("L", "M")


def is_garbage(text: str, has_identifiers: bool) -> bool:
    chars = [c for c in text if not c.isspace()]
    letters = sum(_is_letter(c) for c in chars)
    if letters == 0:
        # "12345" alone may be a customer replying with an order number: vague, not garbage.
        return not has_identifiers
    # Keyboard mash like "!!!!!!!!@@@@####a" is mostly symbols.
    if len(chars) >= 10 and letters / len(chars) < 0.3:
        return True
    # "aaaaaaaaaa" or "?!?!?!?!?!"
    if len(chars) >= 6 and len(set(c.lower() for c in chars)) <= 2:
        return True
    return False


# ---------------------------------------------------------------------------
# 3. PII masking
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Card candidates: 13-19 digits, optionally grouped with spaces or dashes.
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

# Phone numbers have too many formats for one regex, so we list the common ones explicitly.
# Deliberately narrow: an unmasked phone is a privacy miss, but a masked order ID breaks the pipeline.
PHONE_RES = [
    re.compile(r"\+\d{1,3}[\s-]?\(?\d{2,5}\)?[\s-]?\d{3,5}[\s-]?\d{3,5}(?!\d)"),  # +91 98765 43210, +1 (415) 555 0123
    re.compile(r"(?<!\d)\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),               # (415) 555-0123, 415.555.0123
    re.compile(r"(?<![\d#])[6-9]\d{9}(?!\d)"),                                   # 9876543210 (Indian mobile)
]


def luhn_valid(digits: str) -> bool:
    """Card numbers satisfy the Luhn checksum; most random 16-digit numbers (e.g. order IDs) don't."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask_pii(text: str) -> tuple[str, dict[str, str], Identifiers]:
    """Replace PII with numbered placeholders like [EMAIL_1].

    Placeholders (instead of deleting) keep the sentence readable for the LLM ("send it to [EMAIL_1]")
    and let us put the real value back into the draft reply afterwards.
    """
    pii_map: dict[str, str] = {}
    found = Identifiers()
    counters: dict[str, int] = {}

    def placeholder(kind: str, value: str) -> str:
        # Same value -> same placeholder, so "[EMAIL_1] ... [EMAIL_1]" stays consistent.
        for key, existing in pii_map.items():
            if existing == value and key.startswith(f"[{kind}_"):
                return key
        counters[kind] = counters.get(kind, 0) + 1
        key = f"[{kind}_{counters[kind]}]"
        pii_map[key] = value
        return key

    # Cards first: their digit runs would otherwise be partly eaten by the phone patterns.
    def replace_card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            found.has_card_number = True
            return placeholder("CARD", m.group())
        return m.group()  # not a card (probably an order or tracking number): leave it

    text = CARD_RE.sub(replace_card, text)

    def replace_email(m: re.Match) -> str:
        found.emails.append(m.group())
        return placeholder("EMAIL", m.group())

    text = EMAIL_RE.sub(replace_email, text)

    def replace_phone(m: re.Match) -> str:
        found.phones.append(m.group())
        return placeholder("PHONE", m.group())

    for pattern in PHONE_RES:
        text = pattern.sub(replace_phone, text)

    found.emails = _dedupe(found.emails)
    found.phones = _dedupe(found.phones)
    return text, pii_map, found


# ---------------------------------------------------------------------------
# 4. Identifier extraction (order IDs, amounts, dates)
# ---------------------------------------------------------------------------

# "order #A1234", "order number is 98765", "invoice: INV-2024-001", "tracking id AWB123456"
ORDER_KEYWORD_RE = re.compile(
    r"\b(?:order|invoice|ticket|case|ref(?:erence)?|tracking|awb|booking|transaction|txn)\b"
    r"(?:\s*(?:no\.?|number|num|id|#))?(?:\s*(?:is|was))?\s*[:#-]?\s*"
    r"([A-Z0-9][A-Z0-9-]{3,})\b",
    re.IGNORECASE,
)
# Stand-alone forms without a keyword: "#45821", "ORD-55123", "AB123456"
HASH_ID_RE = re.compile(r"#\s?(\d{4,})\b")
CODE_ID_RE = re.compile(r"\b([A-Z]{2,5}-?\d{4,})\b")

_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"   # 1,299.00 / 1,00,000 / 49.99
_CURRENCY_PREFIX = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "rs": "INR", "rs.": "INR",
                    "inr": "INR", "usd": "USD", "eur": "EUR", "gbp": "GBP"}
_CURRENCY_SUFFIX = {"usd": "USD", "dollars": "USD", "bucks": "USD", "eur": "EUR", "euros": "EUR",
                    "gbp": "GBP", "pounds": "GBP", "inr": "INR", "rupees": "INR", "rs": "INR"}
AMOUNT_PREFIX_RE = re.compile(
    rf"(?P<cur>[$€£₹]|\bRs\.?|\bINR|\bUSD|\bEUR|\bGBP)\s?(?P<num>{_NUM})(?P<k>k\b)?", re.IGNORECASE
)
AMOUNT_SUFFIX_RE = re.compile(
    rf"\b(?P<num>{_NUM})(?P<k>k)?\s?(?P<cur>USD|EUR|GBP|INR|dollars|bucks|euros|pounds|rupees|rs)\b",
    re.IGNORECASE,
)

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
DATE_RES = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                                          # 2024-03-15
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"),                            # 15/03/2024
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}(?:,?\s+\d{{4}})?", re.IGNORECASE),  # 5th March
    re.compile(rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?", re.IGNORECASE),         # March 5
    re.compile(
        rf"\b(?:today|tonight|yesterday|tomorrow|day after tomorrow"
        rf"|(?:last|this|next)\s+(?:week|month|year|night|weekend|{_WEEKDAY})"
        rf"|\d+\s+(?:days?|weeks?|months?|hours?)\s+ago"
        rf"|(?:on\s+)?{_WEEKDAY})\b",
        re.IGNORECASE,
    ),
]


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _parse_number(num: str, k: str | None) -> float:
    value = float(num.replace(",", ""))
    return value * 1000 if k else value


def extract_identifiers(text: str) -> Identifiers:
    ids = Identifiers()

    order_ids = [m.group(1) for m in ORDER_KEYWORD_RE.finditer(text)]
    order_ids += [m.group(1) for m in HASH_ID_RE.finditer(text)]
    order_ids += [m.group(1) for m in CODE_ID_RE.finditer(text)]
    # An ID must contain a digit; this drops false hits like "order status" or "case closed".
    order_ids = _dedupe([o for o in order_ids if any(c.isdigit() for c in o)])
    # Different patterns can match fragments of the same ID ("INV-2024" inside "INV-2024-001").
    ids.order_ids = [o for o in order_ids
                     if not any(o != other and o.lower() in other.lower() for other in order_ids)]

    # Amounts: prefix forms first; skip suffix matches that overlap a span we already took.
    taken: list[tuple[int, int]] = []
    for pattern, table in ((AMOUNT_PREFIX_RE, _CURRENCY_PREFIX), (AMOUNT_SUFFIX_RE, _CURRENCY_SUFFIX)):
        for m in pattern.finditer(text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append(m.span())
            ids.amounts.append(Amount(
                raw=m.group().strip(),
                value=_parse_number(m.group("num"), m.group("k")),
                currency=table.get(m.group("cur").lower()),
            ))

    # Dates: same overlap rule, so "5th March 2024" isn't also reported as "March 2024".
    taken = []
    dates = []
    for pattern in DATE_RES:
        for m in pattern.finditer(text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append(m.span())
            dates.append(m.group().strip())
    ids.dates = _dedupe(dates)
    return ids


# ---------------------------------------------------------------------------
# 5. Prompt-injection flag
# ---------------------------------------------------------------------------

# A cheap first line of defence. The real defence is structural: the LLM never decides refunds
# or escalation (rules do), and the message is always wrapped as data in the prompt.
INJECTION_RES = [
    re.compile(r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:instructions?|rules|prompts?|guidelines)\b",
               re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b|\bact\s+as\s+(?:an?\s+)?(?:admin|developer|system)\b", re.IGNORECASE),
    re.compile(r"\b(?:developer|admin|god)\s+mode\b", re.IGNORECASE),
    re.compile(r"</?\s*(?:customer_message|system|instructions?)\s*>", re.IGNORECASE),
]


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_RES)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def preprocess(raw: str | None) -> PreprocessResult:
    raw = raw or ""
    flags: list[str] = []

    text = normalize(raw)
    if len(text) > config.MAX_INPUT_CHARS:
        text = text[: config.MAX_INPUT_CHARS]
        flags.append("truncated_long_input")

    masked, pii_map, pii_ids = mask_pii(text)
    ids = extract_identifiers(masked)
    ids.emails, ids.phones, ids.has_card_number = pii_ids.emails, pii_ids.phones, pii_ids.has_card_number

    word_count = len(text.split())
    has_identifiers = bool(ids.order_ids or ids.amounts or ids.emails or ids.phones)
    skip = False

    if not text:
        flags.append("empty_input")
        skip = True
    elif is_garbage(text, has_identifiers):
        flags.append("garbage_input")
        skip = True
    elif word_count < config.SHORT_MESSAGE_WORDS:
        flags.append("very_short_message")

    if ids.has_card_number:
        # Customers shouldn't send card numbers by email/chat; the reply should tell them so.
        flags.append("card_number_masked")
    if looks_like_injection(text):
        flags.append("possible_prompt_injection")

    return PreprocessResult(
        original_text=raw,
        normalized_text=text,
        masked_text=masked,
        pii_map=pii_map,
        identifiers=ids,
        word_count=word_count,
        flags=flags,
        should_skip_llm=skip,
    )
