"""Guardrails: the safety and fairness checks that run in plain code, not AI (spec section 8).

Why this file exists:
- BEFORE the AI: protected details (age, marital status...) are redacted and
  lines that try to give the AI orders are quarantined (removed and shown to the recruiter).
- AFTER the AI: quotes are checked against the CV, generated text is scanned for
  protected terms, and simple rules raise flags (missing information, salary above
  band, outside Mauritius, career gaps).
Doing these in code means they always behave the same way and can be tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

from core.schemas import CandidateProfile, Flag, IntakeResult, Role

# ===========================================================================
# 1. Redaction of protected details (spec 8.1, Guidelines §2.2 and §9.2)
# ===========================================================================

# Each entry: the label shown to the recruiter, and the pattern that finds it.
# A detail is only redacted when the label is followed by a colon, e.g. "Age: 45".
# That keeps ordinary words like "Agency" or "Health & Safety" safe.
PROTECTED_LABELS: list[tuple[str, str]] = [
    ("Date of birth", r"date\s+of\s+birth|d\.?o\.?b\.?|born"),
    ("Age", r"age"),
    ("Gender", r"gender|sex"),
    ("Marital status", r"marital\s+status|civil\s+status"),
    ("Children", r"children|dependants|dependents"),
    ("Religion", r"religion"),
    ("Nationality", r"nationality"),
    ("Ethnicity", r"ethnicity|race"),
    ("Health", r"health"),
    ("Disability", r"disability"),
    ("Photo", r"photo|photograph"),
]

REDACTED = "[REDACTED]"


def redact_segment(segment: str) -> tuple[str, str | None]:
    """Redact one "Label: value" piece of text.

    Returns the (possibly changed) segment and the label that was redacted, or None.
    """
    for display_label, pattern in PROTECTED_LABELS:
        match = re.match(rf"^(\s*)({pattern})\s*:\s*(.+?)(\s*)$", segment, flags=re.IGNORECASE)
        if match:
            leading_space, label_as_written, _value, trailing_space = match.groups()
            redacted = f"{leading_space}{label_as_written}: {REDACTED}{trailing_space}"
            return redacted, display_label
    return segment, None


def redact_protected_details(text: str) -> tuple[str, list[str]]:
    """Replace protected details with [REDACTED].

    A line can hold several details separated by "|", e.g.
    "Date of birth: 14/03/1979 | Marital status: Married". Each piece is checked.
    Returns the cleaned text and the list of labels that were redacted.
    """
    redacted_labels: list[str] = []
    new_lines: list[str] = []
    for line in text.split("\n"):
        new_segments: list[str] = []
        for segment in line.split("|"):
            new_segment, label = redact_segment(segment)
            if label is not None:
                redacted_labels.append(label)
            new_segments.append(new_segment)
        new_lines.append("|".join(new_segments))
    return "\n".join(new_lines), redacted_labels


# ===========================================================================
# 2. Prompt-injection quarantine (spec 8.2, Guidelines §8.3)
# ===========================================================================

# Patterns for lines that try to give orders to an AI. Matching lines are removed
# from what the AI sees, and kept so the recruiter can read them.
INJECTION_PATTERNS: list[str] = [
    r"\b(ignore|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules)",
    r"\bdisregard\b.*\b(instructions?|prompts?|rules|previous|prior|above)\b",
    r"\b(note|message)\s+(to|for)\s+(the\s+)?(ai|a\.i\.|screening\s+(system|tool)|llm|language\s+model|assistant|chatgpt)\b",
    r"\bsystem\s+prompt\b",
    r"\byou\s+are\s+now\b",
    r"\bnew\s+instructions?\b",
    r"\bgive\b.{0,60}\bscore\s+of\s+100\b",
    r"\brank\s+(this|the|him|her|them|me|my)\b.{0,40}\b(first|top|highest|#1|number\s+one)\b",
    r"\bmeets\s+(every|all)\s+(the\s+)?requirements?\b",
    r"<\|[^|>]*\|>|\[/?INST\]|<</?SYS>>",  # special tokens such as <|system|>
]

INJECTION_FLAG_MESSAGE = (
    "This CV contained hidden instructions aimed at AI tools. "
    "They were removed before analysis."
)


def looks_like_injection(line: str) -> bool:
    """True if the line matches any of the injection patterns."""
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, line, flags=re.IGNORECASE):
            return True
    return False


def quarantine_injections(text: str) -> tuple[str, list[str]]:
    """Remove lines that look like instructions to the AI.

    Returns the cleaned text and the removed lines (to show the recruiter).
    """
    kept_lines: list[str] = []
    quarantined: list[str] = []
    for line in text.split("\n"):
        if looks_like_injection(line):
            quarantined.append(line.strip())
        else:
            kept_lines.append(line)
    return "\n".join(kept_lines), quarantined


# ===========================================================================
# 3. Intake: redaction + quarantine together, run on every CV before the AI
# ===========================================================================


def run_intake(file_name: str, text: str, file_hash: str) -> IntakeResult:
    """Prepare a CV for the AI: redact first, then quarantine.

    Redacting first means a quarantined line can never show protected details either.
    """
    redacted_text, redactions = redact_protected_details(text)
    clean, quarantined = quarantine_injections(redacted_text)
    clean = clean.strip()
    return IntakeResult(
        file_name=file_name,
        content_hash=file_hash,
        clean_text=clean,
        word_count=len(clean.split()),
        redactions=redactions,
        quarantined_text=quarantined,
    )


def intake_flags(intake: IntakeResult) -> list[Flag]:
    """The flags that come from intake: details redacted, and injection found."""
    flags: list[Flag] = []
    if intake.redactions:
        flags.append(
            Flag(
                code="protected_info_redacted",
                severity="info",
                message=f"{len(intake.redactions)} protected details removed before analysis: "
                + ", ".join(intake.redactions)
                + ".",
                guideline="§2.2",
            )
        )
    if intake.quarantined_text:
        flags.append(
            Flag(
                code="prompt_injection",
                severity="critical",
                message=INJECTION_FLAG_MESSAGE,
                guideline="§8.3",
            )
        )
    return flags


# ===========================================================================
# 4. Quote verification (spec 8.3, Guidelines §3.1 and §8.4)
# ===========================================================================

QUOTE_SIMILARITY_THRESHOLD: float = 0.85


def normalise_for_matching(text: str) -> str:
    """Lowercase, remove punctuation (except %), and collapse spaces.

    Both the quote and the CV get the same treatment, so small differences
    such as a comma or a line break don't matter.
    """
    lowered = text.lower()
    without_punctuation = re.sub(r"[^\w\s%]", " ", lowered)
    return re.sub(r"\s+", " ", without_punctuation).strip()


def is_quote_verified(quote: str | None, cv_text: str) -> bool:
    """True if the quote really appears in the CV.

    It passes if it appears exactly, or if a stretch of the CV with the same
    number of words is at least 85% similar (allowing one changed word or so).
    An empty quote never passes.
    """
    if quote is None:
        return False
    normal_quote = normalise_for_matching(quote)
    if normal_quote == "":
        return False
    normal_cv = normalise_for_matching(cv_text)
    if normal_quote in normal_cv:
        return True

    quote_words = normal_quote.split()
    cv_words = normal_cv.split()
    window_size = len(quote_words)
    # Slide a window of the same number of words over the CV and compare.
    for start in range(0, max(1, len(cv_words) - window_size + 1)):
        window = " ".join(cv_words[start : start + window_size])
        similarity = SequenceMatcher(None, normal_quote, window).ratio()
        if similarity >= QUOTE_SIMILARITY_THRESHOLD:
            return True
    return False


# ===========================================================================
# 5. Salary (spec 8.4, Guidelines §6.1)
# ===========================================================================

SALARY_TOLERANCE: float = 0.15  # flag when more than 15% above the top of the band
MIN_SALARY_VALUE: float = 1000  # smaller numbers (like "2" in "2 months") are ignored

# Currency markers that are NOT Mauritian rupees. Checked in this order.
FOREIGN_CURRENCY_PATTERNS: list[tuple[str, str]] = [
    ("ZAR", r"\bZAR\b|\bR\s?\d"),
    ("USD", r"\bUSD\b|US\$|\$"),
    ("EUR", r"\bEUR\b|€"),
    ("GBP", r"\bGBP\b|£"),
]

# A number like 75,000 or 75 000 or 55 or 1.5, optionally followed by k or million.
NUMBER_PATTERN = r"(\d{1,3}(?:[,  ]\d{3})+|\d+(?:\.\d+)?)\s*(k|K|million)?\b"


def parse_salary(text: str | None) -> float | None:
    """Read a salary figure from text: "MUR 75,000" -> 75000, "55k" -> 55000.

    Takes the first number of 1,000 or more. Returns None if there's no such
    number (e.g. "Open to discussion").
    """
    if not text:
        return None
    for match in re.finditer(NUMBER_PATTERN, text):
        number_text, multiplier = match.groups()
        digits = re.sub(r"[,  ]", "", number_text)
        value = float(digits)
        if multiplier in ("k", "K"):
            value = value * 1000
        elif multiplier == "million":
            value = value * 1_000_000
        if value >= MIN_SALARY_VALUE:
            return value
    return None


def detect_foreign_currency(text: str | None) -> str | None:
    """Return a currency code (e.g. "ZAR") if the salary isn't in Mauritian rupees.

    No currency marker means MUR (spec section 21: values are MUR unless stated).
    """
    if not text:
        return None
    for currency_code, pattern in FOREIGN_CURRENCY_PATTERNS:
        if re.search(pattern, text):
            return currency_code
    return None


def check_salary(salary_text: str | None, salary_max: float | None) -> list[Flag]:
    """Flag a salary expectation that is above the band or in another currency.

    A high salary is only flagged for the hiring manager, never a reason to reject.
    """
    value = parse_salary(salary_text)
    if value is None:
        return []  # no figure: handled by the missing-information check

    foreign_currency = detect_foreign_currency(salary_text)
    if foreign_currency is not None:
        return [
            Flag(
                code="salary_not_comparable",
                severity="info",
                message=f"Salary expectation is in {foreign_currency}, so it can't be compared with the MUR band.",
                guideline="§6.1",
            )
        ]

    if salary_max is None:
        return []
    # round() because computers store 1.15 slightly imprecisely: 60000 * 1.15
    # would otherwise give 68999.99999 and wrongly flag a salary of exactly 69,000.
    limit = round(salary_max * (1 + SALARY_TOLERANCE), 2)
    if value <= limit:
        return []

    percent_above = round((value - salary_max) / salary_max * 100)
    return [
        Flag(
            code="salary_above_band",
            severity="warning",
            message=(
                f"Expectation MUR {value:,.0f} is {percent_above}% above the band. "
                "Flag for the hiring manager, don't reject."
            ),
            guideline="§6.1",
        )
    ]


# ===========================================================================
# 6. Outside Mauritius (spec 8.4, Guidelines §6.2)
# ===========================================================================

MAURITIAN_PLACES: list[str] = [
    "mauritius", "port louis", "quatre bornes", "rose hill", "beau bassin", "curepipe",
    "vacoas", "phoenix", "moka", "ebene", "mahebourg", "flacq", "goodlands", "triolet",
    "grand baie", "pamplemousses", "tamarin", "flic en flac", "black river", "riviere noire",
    "rose belle", "souillac", "floreal", "bel air", "riviere du rempart", "saint pierre",
    "st pierre", "plaine magnien", "le hochet", "terre rouge", "rodrigues", "pereybere",
]


def mentions_mauritian_place(location: str) -> bool:
    lowered = location.lower().replace("-", " ")
    for place in MAURITIAN_PLACES:
        if place in lowered:
            return True
    return False


def has_foreign_phone_code(phone: str | None) -> bool:
    """True if the phone has an international code that isn't +230 (Mauritius).

    A local number written without a code (e.g. "5712 3344") isn't counted as foreign.
    """
    if not phone:
        return False
    compact = re.sub(r"[\s\-().]", "", phone)
    if compact.startswith("+"):
        return not compact.startswith("+230")
    if compact.startswith("00"):
        return not compact.startswith("00230")
    return False


def is_outside_mauritius(location: str | None, phone: str | None) -> bool:
    """True if the location names no Mauritian place, or the phone code isn't +230."""
    location_is_foreign = bool(location) and not mentions_mauritian_place(location or "")
    return location_is_foreign or has_foreign_phone_code(phone)


def check_location(location: str | None, phone: str | None) -> list[Flag]:
    if not is_outside_mauritius(location, phone):
        return []
    return [
        Flag(
            code="outside_mauritius",
            severity="info",
            message="Based outside Mauritius. Ask about work permit status and relocation timeline.",
            guideline="§6.2",
        )
    ]


# ===========================================================================
# 7. Years of experience and career gaps (spec 5.3 and 8.4, Guidelines §2.3)
# ===========================================================================

MAX_GAP_MONTHS: int = 6  # gaps longer than this become a neutral question

MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

PRESENT_WORDS: set[str] = {"present", "current", "now", "today", "ongoing", "date"}

CAREER_BREAK_PATTERN = r"\bcareer\s+(break|gap)\b|\bsabbatical\b"


@dataclass
class MonthSpan:
    """A role's dates as month numbers (year * 12 + month), end included."""

    start: int
    end: int
    is_marketing: bool
    is_break: bool
    label: str


def month_number(year: int, month: int) -> int:
    """Turn a year and month into one counting number, so we can subtract dates."""
    return year * 12 + (month - 1)


def month_label(number: int) -> str:
    """Turn a month number back into text like "Jan 2023"."""
    year = number // 12
    month = number % 12 + 1
    month_name = list(MONTHS.keys())[month - 1].capitalize()
    return f"{month_name} {year}"


def parse_month(text: str | None, today: date, is_end: bool) -> int | None:
    """Read a date as written on a CV: "Feb 2022", "February 2022", "02/2022", "2022" or "Present".

    A bare year means January for a start date and December for an end date.
    Returns None if the date can't be read.
    """
    if text is None:
        return None
    cleaned = text.strip().lower()
    if cleaned in PRESENT_WORDS:
        return month_number(today.year, today.month)

    # "Feb 2022" or "February 2022"
    match = re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{4})", cleaned)
    if match and match.group(1) in MONTHS:
        return month_number(int(match.group(2)), MONTHS[match.group(1)])

    # "02/2022" or "2-2022"
    match = re.fullmatch(r"(\d{1,2})\s*[/\-.]\s*(\d{4})", cleaned)
    if match and 1 <= int(match.group(1)) <= 12:
        return month_number(int(match.group(2)), int(match.group(1)))

    # "2022"
    match = re.fullmatch(r"(\d{4})", cleaned)
    if match:
        month = 12 if is_end else 1
        return month_number(int(match.group(1)), month)
    return None


def is_career_break(role: Role) -> bool:
    return re.search(CAREER_BREAK_PATTERN, role.title, flags=re.IGNORECASE) is not None


def roles_to_spans(roles: list[Role], today: date) -> list[MonthSpan] | None:
    """Convert every role's dates to month numbers. None if any date can't be read."""
    spans: list[MonthSpan] = []
    for role in roles:
        start = parse_month(role.start, today, is_end=False)
        end = parse_month(role.end, today, is_end=True)
        if start is None or end is None or end < start:
            return None
        label = f"{role.title} ({role.start} - {role.end})"
        spans.append(MonthSpan(start, end, role.is_marketing_role, is_career_break(role), label))
    return spans


def merge_spans(spans: list[MonthSpan]) -> list[tuple[int, int]]:
    """Join overlapping or back-to-back periods so no month is counted twice."""
    periods = sorted((span.start, span.end) for span in spans)
    merged: list[tuple[int, int]] = []
    for start, end in periods:
        if merged and start <= merged[-1][1] + 1:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def count_years(spans: list[MonthSpan]) -> float:
    """Total years covered by the spans (overlaps counted once), to 1 decimal."""
    total_months = 0
    for start, end in merge_spans(spans):
        total_months += end - start + 1  # +1 because the end month is included
    return round(total_months / 12, 1)


@dataclass
class ExperienceYears:
    total: float
    relevant: float


def compute_experience_years(roles: list[Role], today: date) -> ExperienceYears | None:
    """Work out total and relevant (marketing) years from role dates.

    Career breaks are left out and overlapping roles are counted once.
    Returns None if any date can't be read, so the caller can use the AI's numbers.
    """
    spans = roles_to_spans(roles, today)
    if spans is None or len(spans) == 0:
        return None
    work_spans = [span for span in spans if not span.is_break]
    marketing_spans = [span for span in work_spans if span.is_marketing]
    return ExperienceYears(total=count_years(work_spans), relevant=count_years(marketing_spans))


def find_career_gaps(roles: list[Role], today: date) -> list[str]:
    """List career breaks, and gaps of more than 6 months between roles.

    These are facts for a neutral interview question, never a weakness (Guidelines §2.3).
    """
    spans = roles_to_spans(roles, today)
    if spans is None:
        return []

    gaps: list[str] = []
    for span in spans:
        if span.is_break:
            gaps.append(f"Career break: {span.label}")

    # Look for empty time between periods. Career breaks count as covered time,
    # so the same gap isn't reported twice.
    merged = merge_spans(spans)
    for index in range(1, len(merged)):
        previous_end = merged[index - 1][1]
        next_start = merged[index][0]
        months_between = next_start - previous_end - 1
        if months_between > MAX_GAP_MONTHS:
            gaps.append(
                f"Gap of {months_between} months between {month_label(previous_end)} "
                f"and {month_label(next_start)}"
            )
    return gaps


def career_gap_flags(gaps: list[str]) -> list[Flag]:
    if not gaps:
        return []
    return [
        Flag(
            code="career_gap",
            severity="info",
            message="Career gap noted ("
            + "; ".join(gaps)
            + "). Ask a neutral question. Never treat it as a weakness.",
            guideline="§2.3",
        )
    ]


def mentions_career_gap(text: str) -> bool:
    """True if a sentence talks about a career gap or break. Such concerns are removed (§2.3).

    Only gaps in someone's work history count, so "a skills gap in SEO" is left alone.
    """
    pattern = (
        r"\bcareer\s+(gap|break)s?\b"
        r"|\b(employment|work|cv|resume)\s+(gap|break)s?\b"
        r"|\bgaps?\s+(in|between)\s+(employment|roles|jobs|work|career)\b"
        r"|\bbreak\s+from\s+work\b|\btime\s+off\b|\bunemploy"
    )
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


# ===========================================================================
# 8. Missing information (spec 8.4, Guidelines §5.1 and §5.2)
# ===========================================================================

# Written answers that mean "nothing given".
EMPTY_ANSWERS: set[str] = {"", "none", "null", "n/a", "na", "-", "not stated", "not provided", "unknown"}


def is_missing(value: str | None) -> bool:
    return value is None or value.strip().lower() in EMPTY_ANSWERS


def find_missing_info(profile: CandidateProfile) -> list[str]:
    """List the required details that are missing, using the keys from ground_truth.json.

    A salary expectation only counts if it contains a number ("Open to discussion" is missing).
    References are not included: they can be collected later (§5.2).
    """
    missing: list[str] = []
    if is_missing(profile.email):
        missing.append("email")
    if is_missing(profile.phone):
        missing.append("phone")
    salary = profile.salary_expectation
    if is_missing(salary) or not re.search(r"\d", salary or ""):
        missing.append("salary_expectation")
    if is_missing(profile.notice_period):
        missing.append("notice_period")
    if is_missing(profile.right_to_work):
        missing.append("right_to_work")
    return missing


MISSING_INFO_NAMES: dict[str, str] = {
    "email": "email",
    "phone": "phone number",
    "salary_expectation": "salary expectation",
    "notice_period": "notice period or availability",
    "right_to_work": "right to work in Mauritius",
}


def missing_info_flags(profile: CandidateProfile) -> list[Flag]:
    """Flags for missing required details, and an info note if references come later."""
    flags: list[Flag] = []
    missing = find_missing_info(profile)
    if missing:
        names = [MISSING_INFO_NAMES[key] for key in missing]
        flags.append(
            Flag(
                code="missing_info",
                severity="warning",
                message="Missing before interview: " + ", ".join(names)
                + ". Ask the candidate. This is not a reason to reject.",
                guideline="§5.1",
            )
        )
    if is_missing(profile.references):
        flags.append(
            Flag(
                code="references_later",
                severity="info",
                message="No references given. They can be collected later.",
                guideline="§5.2",
            )
        )
    return flags


# ===========================================================================
# 9. Bias filter for AI-written text (spec 8.5, Guidelines §7.3)
# ===========================================================================

PROTECTED_TERMS_PATTERN = (
    r"\b("
    r"age|aged|years\s+old|older|younger"
    r"|married|marital|spouse|husband|wife|children|kids|pregnant|pregnancy|family\s+plans"
    r"|religion|religious|church|mosque|temple|kovil|synagogue|place\s+of\s+worship"
    r"|nationality|ethnicity|ethnic|race|racial|gender"
    r"|disability|disabled|date\s+of\s+birth|born\s+in"
    r")\b"
)


def contains_protected_term(text: str) -> bool:
    return re.search(PROTECTED_TERMS_PATTERN, text, flags=re.IGNORECASE) is not None


def filter_protected_items(items: list[str]) -> tuple[list[str], list[str]]:
    """Split a list (e.g. interview questions) into (kept, removed)."""
    kept: list[str] = []
    removed: list[str] = []
    for item in items:
        if contains_protected_term(item):
            removed.append(item)
        else:
            kept.append(item)
    return kept, removed


def filter_protected_sentences(text: str) -> tuple[str, list[str]]:
    """Remove sentences that mention a protected term. Returns (clean text, removed sentences)."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept, removed = filter_protected_items(sentences)
    return " ".join(kept), removed


def bias_filtered_flag(removed: list[str]) -> list[Flag]:
    if not removed:
        return []
    return [
        Flag(
            code="bias_filtered",
            severity="info",
            message=f"{len(removed)} AI-written sentence(s) or question(s) removed because they "
            "mentioned a protected characteristic.",
            guideline="§7.3",
        )
    ]
