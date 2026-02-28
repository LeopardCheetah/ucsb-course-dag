"""
parse_prerequisites.py
----------------------
Single function: parse_prerequisites(raw) -> (required, concurrent)

Schema
------
required   : list of OR-groups.  Each group is a list of course code strings.
             Within a group, any ONE satisfies the requirement (OR).
             All groups together must be satisfied (AND across groups).
             e.g. [["CMPSC 40", "MATH 8"], ["CMPSC 24"]]
                  means (CMPSC 40 or MATH 8) AND (CMPSC 24)

concurrent : same structure, for courses that may be taken simultaneously.

([], []) means no prerequisites of either type.

Usage
-----
    from parse_prerequisites import parse_prerequisites

    course.prerequisites = parse_prerequisites(c["prerequisites_raw"])
"""

import re


# ── Department abbreviation normalization ─────────────────────────────────────
# Maps every known long-form or alternate spelling (uppercased) to the canonical
# abbreviation used in course codes.  Add entries here as needed.

_DEPT_ALIASES = {
    # Mathematics
    "MATHEMATICS":                          "MATH",
    "MATH":                                 "MATH",
    # Computer Science
    "COMPUTERSCIENCE":                      "CMPSC",
    "COMPUTER SCIENCE":                     "CMPSC",
    "CMPSC":                                "CMPSC",
    "CS":                                   "CMPSC",
    # Electrical & Computer Engineering
    "ELECTRICAL AND COMPUTER ENGINEERING":  "ECE",
    "ELECTRICAL ENGINEERING":               "ECE",
    "ECE":                                  "ECE",
    # Physics
    "PHYSICS":                              "PHYS",
    "PHYS":                                 "PHYS",
    # Statistics & Applied Probability
    "STATISTICS AND APPLIED PROBABILITY":   "PSTAT",
    "STATISTICS":                           "PSTAT",
    "PSTAT":                                "PSTAT",
    # Engineering (general / Engineering Sciences)
    "ENGINEERING SCIENCES":                 "ENGR",
    "ENGINEERING":                          "ENGR",
    "ENGR":                                 "ENGR",
    # Chemistry & Biochemistry
    "CHEMISTRY AND BIOCHEMISTRY":           "CHEM",
    "CHEMISTRY":                            "CHEM",
    "CHEM":                                 "CHEM",
    # Economics
    "ECONOMICS":                            "ECON",
    "ECON":                                 "ECON",
    # Biology
    "BIOLOGY":                              "BIOL",
    "BIOL":                                 "BIOL",
    # Mechanical Engineering
    "MECHANICAL ENGINEERING":               "ME",
    "ME":                                   "ME",
    # Materials
    "MATERIALS":                            "MATRL",
    "MATRL":                                "MATRL",
    # Chemical Engineering
    "CHEMICAL ENGINEERING":                 "CHE",
    "CHE":                                  "CHE",
}


def _normalize_dept(raw_dept: str) -> str:
    """
    Normalize a matched department token to its canonical abbreviation.
    e.g. "Mathematics" -> "MATH", "Engineering" -> "ENGR", "CS" -> "CMPSC".
    Falls back to uppercasing as-is if no alias is found.
    """
    return _DEPT_ALIASES.get(raw_dept.upper(), raw_dept.upper())


# ── Regexes ───────────────────────────────────────────────────────────────────

# Matches an explicit "DEPT NUMBER" token.
# The dept portion optionally matches TWO words so that multi-word department
# names like "Computer Science 48" or "Electrical Engineering 3" are captured
# as a single dept token rather than just the last word before the number.
_DEPT_NUM_RE = re.compile(
    r'\b([A-Z][A-Za-z]{1,12}(?:\s+[A-Za-z]{1,12})?)\s+(\d+[A-Za-z]{0,4})\b'
)

# Bare course number (possibly with letter suffix) not preceded by another digit.
# e.g. "4BI", "6A", "6B" in "Math 4B or 4BI, 6A, and 6B".
_BARE_NUM_RE = re.compile(r'(?<!\d)\b(\d+[A-Za-z]{0,4})\b')

# Splits on "or" but NOT "or better/equivalent/higher/above/consent/permission"
_OR_RE = re.compile(
    r'\bor\b(?!\s+(?:better|equivalent|higher|above|consent|permission))',
    re.IGNORECASE,
)

# Concurrent-enrollment phrasing
_CONCURRENT_RE = re.compile(
    r'may be taken concurrently|concurrent(?:ly)?|concurrent enrollment',
    re.IGNORECASE,
)

# Catalog page footer — everything from here on is boilerplate noise
_FOOTER_RE = re.compile(r'UC Santa Barbara\s+Santa Barbara', re.IGNORECASE)

# "Pre-requisite(s):" label prefix
_PREFIX_RE = re.compile(r'^Pre-?requisites?\s*:\s*', re.IGNORECASE)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _clean(raw):
    """Strip the boilerplate footer and any label prefix."""
    m = _FOOTER_RE.search(raw)
    if m:
        raw = raw[:m.start()]
    raw = _PREFIX_RE.sub('', raw)
    return raw.strip().strip('"').strip()


def _is_noise(text):
    """True for clauses with no course codes (no digit = no course code)."""
    if not re.search(r'\d', text):
        return True
    if re.search(r'\bconsent\b', text, re.IGNORECASE):
        return True
    return False


def _extract_courses(text, fallback_dept):
    """
    Pull all course codes out of a short text fragment.
    Bare numbers (e.g. "4BI") inherit fallback_dept from the last seen dept.
    Returns (list_of_codes, last_dept_seen).
    """
    results   = []
    last_dept = fallback_dept

    explicit = list(_DEPT_NUM_RE.finditer(text))
    covered  = set()

    for m in explicit:
        dept = _normalize_dept(m.group(1))
        num  = m.group(2).upper()
        code = f"{dept} {num}"
        if code not in results:
            results.append(code)
        last_dept = dept
        covered.update(range(m.start(), m.end()))

    # Bare numbers not already part of an explicit match
    for m in _BARE_NUM_RE.finditer(text):
        if m.start() in covered:
            continue
        if last_dept:
            code = f"{last_dept} {m.group(1).upper()}"
            if code not in results:
                results.append(code)

    return results, last_dept


def _split_or_group(text, fallback_dept):
    """
    Split text on "or" and extract course codes from each alternative.
    Returns (or_group_list, last_dept_seen).
    """
    codes     = []
    last_dept = fallback_dept
    for alt in _OR_RE.split(text):
        extracted, last_dept = _extract_courses(alt.strip(), last_dept)
        codes.extend(c for c in extracted if c not in codes)
    return codes, last_dept


def _split_into_and_groups(clause):
    """
    Split a semicolon-clause into AND sub-groups on " and " and ",".
    Dept inheritance in _extract_courses handles bare numbers across splits.
    """
    groups = []
    for part in re.split(r'\s+and\s+', clause, flags=re.IGNORECASE):
        for token in part.split(','):
            token = token.strip()
            if token:
                groups.append(token)
    return groups


# ── Public API ────────────────────────────────────────────────────────────────

def parse_prerequisites(raw):
    """
    Parse a raw prerequisite string into (required_groups, concurrent_groups).

    Each is a list of OR-groups; each OR-group is a list of course-code strings.
    Within an OR-group, ANY ONE course satisfies that slot.
    Across OR-groups, ALL must be satisfied (AND logic).

    Returns ([], []) when there are no prerequisites.
    """
    if not raw:
        return ([], [])

    cleaned = _clean(raw)
    if not cleaned:
        return ([], [])

    if re.match(r'^consent of instructor\.?$', cleaned, re.IGNORECASE):
        return ([], [])

    required   = []
    concurrent = []
    last_dept  = None  # carries across semicolons for dept inheritance

    # Level 1 — semicolons are top-level AND separators
    for sc in [c.strip() for c in re.split(r';', cleaned) if c.strip()]:
        if _is_noise(sc):
            continue
        sc = re.sub(r'^and\s+', '', sc, flags=re.IGNORECASE).strip()

        # Level 2 — commas and "and" are AND separators within a clause
        for group_text in _split_into_and_groups(sc):
            group_text = re.sub(r'^and\s+', '', group_text, flags=re.IGNORECASE).strip()
            if not group_text or _is_noise(group_text):
                continue

            # Concurrent flag is per-group so that "Math 3A (may be taken
            # concurrently), CS 8" only marks Math 3A as concurrent.
            is_conc = bool(_CONCURRENT_RE.search(group_text))
            group_clean = re.sub(
                r'\(?\s*may be taken concurrently[^)]*\)?\s*',
                '', group_text, flags=re.IGNORECASE,
            ).strip().rstrip(',').strip()

            if not group_clean or _is_noise(group_clean):
                continue

            # Level 3 — "or" separates alternatives within an AND group
            or_group, last_dept = _split_or_group(group_clean, last_dept)
            if not or_group:
                continue

            if is_conc:
                concurrent.append(or_group)
            else:
                required.append(or_group)

    return (required, concurrent)