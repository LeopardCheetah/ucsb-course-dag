"""
UCSB Course Catalog Scraper
============================
Scrapes all undergraduate (course number < 200) MATH, CMPSC, and ECE courses.

Course Discovery:
  Fetches plain-HTML department pages from the old UCSB General Catalog
  (my.sa.ucsb.edu) as the authoritative ground-truth course list.

Course Details:
  Fetches each course's individual page on catalog.ucsb.edu and parses
  the Coursedog markdown output for title, description, units, and prereqs.

Prerequisite parsing:
  - Concurrent prereqs  : "(may be taken concurrently)" -> flagged as concurrent
  - Conditional prereqs : "or" between course codes     -> stored as OR groups
  - Required prereqs    : comma/semicolon separated     -> each is its own group

Features:
  - Rotating User-Agent headers to avoid bot detection
  - Exponential backoff + hard cooldown on rate-limit responses
  - Checkpoint file: auto-saves after every course; re-run resumes where it left off
  - Ctrl+C graceful shutdown: always saves progress before exiting

Output: ucsb_courses.json
"""

import json
import os
import random
import re
import sys
import time

import requests
from html.parser import HTMLParser

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

DEPARTMENTS = {
    "MATH":  "Mathematics",
    "CMPSC": "Computer Science",
    "ECE":   "Electrical and Computer Engineering",
}

# Old UCSB General Catalog pages — plain HTML, no JS, list every real course.
# Multiple years are tried so recently added/removed courses are both captured.
DISCOVERY_URLS = {
    "MATH": [
        "https://my.sa.ucsb.edu/catalog/Current/CollegesDepartments/ls-intro/math.aspx?DeptTab=Courses",
        "https://my.sa.ucsb.edu/catalog/2021-2022/CollegesDepartments/ls-intro/math.aspx?DeptTab=Courses",
    ],
    "CMPSC": [
        "https://my.sa.ucsb.edu/catalog/Current/CollegesDepartments/coe/compsci-engr.aspx?DeptTab=Courses",
        "https://my.sa.ucsb.edu/catalog/2021-2022/CollegesDepartments/coe/compsci-engr.aspx?DeptTab=Courses",
    ],
    "ECE": [
        "https://my.sa.ucsb.edu/catalog/Current/CollegesDepartments/coe/ece.aspx?DeptTab=Courses",
        "https://my.sa.ucsb.edu/catalog/2019-2020/CollegesDepartments/coe/ece.aspx?DeptTab=Courses",
    ],
}

# Regex patterns to extract course codes from the discovery pages
COURSE_CODE_PATTERNS = {
    "MATH":  re.compile(r'\bMATH\s+(\d+[A-Z]{0,4})\b'),
    "CMPSC": re.compile(r'\bCMPSC\s+(\d+[A-Z]{0,6})\b'),
    "ECE":   re.compile(r'\bECE\s+(\d+[A-Z]{0,4})\b'),
}

# Only scrape undergraduate courses (course number strictly below this threshold)
UNDERGRAD_MAX_COURSE_NUMBER = 200

CATALOG_BASE    = "https://catalog.ucsb.edu/courses"
REQUEST_DELAY   = 1.0    # base seconds between requests — be polite
REQUEST_TIMEOUT = 15

# Output files
OUTPUT_FILE     = "ucsb_courses.json"
CHECKPOINT_FILE = "ucsb_courses_checkpoint.json"

# How long to pause (seconds) after all per-attempt retries are exhausted
HARD_COOLDOWN = 90

# ─────────────────────────────────────────────────────────────
# HTTP helpers — rotating headers, session, backoff
# ─────────────────────────────────────────────────────────────

USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13.6; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Firefox on Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Safari on iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    # Chrome on Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.64 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.144 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
]

RETRY_STATUS_CODES = {405, 429, 500, 502, 503, 504}
MAX_RETRIES        = 4
BACKOFF_BASE       = 2.0  # wait = BACKOFF_BASE ** attempt  =>  2s, 4s, 8s, 16s

# Persistent session — reuses TCP connections and carries cookies like a real browser
_session = requests.Session()


def _make_headers() -> dict:
    """Return a fresh headers dict with a randomly chosen User-Agent."""
    return {
        "User-Agent":                random.choice(USER_AGENTS),
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Referer":                   "https://catalog.ucsb.edu/courses",
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _get(url: str) -> requests.Response:
    """
    GET a URL with rotating headers, polite delay, and exponential backoff
    on rate-limit / server-error responses (405, 429, 5xx).
    """
    for attempt in range(MAX_RETRIES):
        _session.headers.update(_make_headers())
        try:
            r = _session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            raise

        if r.status_code not in RETRY_STATUS_CODES:
            time.sleep(REQUEST_DELAY + random.uniform(0.1, 0.6))
            return r

        wait = (BACKOFF_BASE ** (attempt + 1)) + random.uniform(0, 1)
        print(
            f"\n  [RATE LIMIT] HTTP {r.status_code} — waiting {wait:.1f}s "
            f"(attempt {attempt + 1}/{MAX_RETRIES})...",
            file=sys.stderr,
        )
        time.sleep(wait)

    # Final attempt after all backoffs exhausted
    _session.headers.update(_make_headers())
    r = _session.get(url, timeout=REQUEST_TIMEOUT)
    time.sleep(REQUEST_DELAY + random.uniform(0.1, 0.6))
    return r


# ─────────────────────────────────────────────────────────────
# Checkpoint helpers
# ─────────────────────────────────────────────────────────────

def _load_checkpoint() -> dict:
    """Load previously scraped courses from the checkpoint file, if it exists."""
    if not os.path.exists(CHECKPOINT_FILE):
        return {}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        total = sum(len(v) for v in data.values())
        print(f"  Resuming from checkpoint: {total} courses already scraped.")
        return data
    except Exception as e:
        print(f"  [WARN] Could not read checkpoint ({e}). Starting fresh.")
        return {}


def _save_checkpoint(all_courses: dict) -> None:
    """Atomically write the checkpoint file (write to .tmp then rename)."""
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(all_courses, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CHECKPOINT_FILE)


def _write_output(all_courses: dict) -> None:
    """Write the final output JSON file."""
    total = sum(len(v) for v in all_courses.values())
    payload = {
        "source":           "https://catalog.ucsb.edu",
        "discovery_source": "https://my.sa.ucsb.edu/catalog (plain-HTML dept pages)",
        "departments":      list(DEPARTMENTS.keys()),
        "total_courses":    total,
        "prerequisite_format": {
            "description": (
                "Each course has a 'prerequisites' object with three keys: "
                "'required', 'concurrent', and 'raw'. "
                "'required' is a list of groups; within each group any ONE course "
                "satisfies the requirement (OR/conditional). All groups must be "
                "satisfied (AND). "
                "'concurrent' lists courses that may be taken simultaneously. "
                "'raw' is the original unmodified prerequisite text."
            ),
            "example": {
                "required":   [["MATH 3A", "MATH 2A"], ["CMPSC 8"]],
                "concurrent": ["MATH 3A"],
                "raw":        "Math 3A or 2A with grade C or better (may be taken concurrently), CMPSC 8",
            },
        },
        "courses": all_courses,
    }
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, OUTPUT_FILE)
    print(f"  Saved {total} courses to '{OUTPUT_FILE}'")


# ─────────────────────────────────────────────────────────────
# Phase 1 — Discover real course codes from the old catalog
# ─────────────────────────────────────────────────────────────

def _course_sort_key(code: str, subject: str) -> tuple:
    """Sort courses numerically: MATH 3A < MATH 4A < MATH 34B < MATH 100A."""
    rest = code[len(subject):].strip()
    m = re.match(r'^(\d+)([A-Z]*)$', rest)
    if m:
        return (int(m.group(1)), m.group(2))
    return (9999, rest)


def discover_courses_from_catalog_pages(subject: str) -> list:
    """
    Fetch the department's course listing page(s) from the old plain-HTML
    UCSB catalog and extract every course code mentioned there.
    Returns a sorted list of strings like ["CMPSC 16", "CMPSC 24", ...].
    """
    pattern = COURSE_CODE_PATTERNS[subject]
    found = set()

    for url in DISCOVERY_URLS[subject]:
        try:
            r = _get(url)
            if r.status_code != 200:
                print(
                    f"  [WARN] Discovery page returned HTTP {r.status_code}: {url}",
                    file=sys.stderr,
                )
                continue

            numbers = pattern.findall(r.text)
            before = len(found)
            for num in numbers:
                course_num = int(re.match(r'\d+', num).group())
                if course_num < UNDERGRAD_MAX_COURSE_NUMBER:
                    found.add(f"{subject} {num}")
            added = len(found) - before
            skipped = len(numbers) - added
            suffix = (
                f"  (skipped {skipped} grad courses >= {UNDERGRAD_MAX_COURSE_NUMBER})"
                if skipped else ""
            )
            print(f"  + {added} undergrad codes from: {url}{suffix}")

        except requests.RequestException as e:
            print(f"  [WARN] Could not reach discovery page: {e}", file=sys.stderr)

    if not found:
        print(
            f"  [WARN] No courses discovered for {subject}. "
            "Check that the discovery URLs are reachable.",
            file=sys.stderr,
        )

    return sorted(found, key=lambda c: _course_sort_key(c, subject))


# ─────────────────────────────────────────────────────────────
# Phase 2 — Parse course pages
# ─────────────────────────────────────────────────────────────

class _CoursePageParser(HTMLParser):
    """
    Parses the raw HTML returned by requests.get() for a Coursedog course page.

    Collects:
      self.sections  -- dict mapping h3 label -> concatenated <p> text beneath it
      self.h2_texts  -- list of all h2 text strings (used to extract the course title)
    """
    def __init__(self):
        super().__init__()
        self.sections = {}
        self.h2_texts = []
        self._cur_section = None
        self._in_tag = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ('h1', 'h2', 'h3', 'p'):
            self._in_tag = tag
            self._buf = []

    def handle_endtag(self, tag):
        if tag != self._in_tag:
            return
        text = ''.join(self._buf).strip()
        if tag == 'h3':
            self._cur_section = text
        elif tag in ('h1', 'h2'):
            if text:
                self.h2_texts.append(text)
            self._cur_section = None
        elif tag == 'p':
            if text and self._cur_section:
                existing = self.sections.get(self._cur_section, '')
                self.sections[self._cur_section] = (existing + ' ' + text).strip()
        self._in_tag = None
        self._buf = []

    def handle_data(self, data):
        if self._in_tag:
            self._buf.append(data)


def _parse_sections(html: str) -> dict:
    """Parse a Coursedog course page and return the h3-keyed sections dict."""
    p = _CoursePageParser()
    p.feed(html)
    return p.sections


def _extract_title(html: str, course_code: str) -> str:
    """
    Extract the course title from the <h2> heading like "MATH 137A - Graph and Network Theory".
    Falls back to the "Full Course Title" h3 section if the h2 pattern doesn't match.
    """
    p = _CoursePageParser()
    p.feed(html)
    # Try to find an h2 that starts with the course code followed by a dash/en-dash
    pattern = re.compile(
        r'^'  + re.escape(course_code) + r'\s*[-\u2013\u2014]\s*(.+)$'
    )
    for h2 in p.h2_texts:
        m = pattern.match(h2)
        if m:
            return m.group(1).strip()
    # Fallback: use the Full Course Title section
    return p.sections.get('Full Course Title', '')


# ─────────────────────────────────────────────────────────────
# Prerequisite Parsing
# ─────────────────────────────────────────────────────────────

# Maps full/alternate department names to canonical abbreviations
DEPT_ALIASES = {
    r'(?:Computer\s+Science|CS|CMPSC)':                              'CMPSC',
    r'(?:Math(?:ematics)?)':                                         'MATH',
    r'(?:ECE|Electrical(?:\s+(?:and\s+)?Computer\s+Engineering)?)':  'ECE',
    r'(?:PHYS|Physics)':                                             'PHYS',
    r'(?:PSTAT|Statistics(?:\s+&\s+Applied\s+Probability)?)':        'PSTAT',
    r'(?:ENGR|Engineering(?:\s+Sciences)?)':                         'ENGR',
    r'(?:CHEM|Chemistry(?:\s+and\s+Biochemistry)?)':                 'CHEM',
}

# Matches "CMPSC 16", "Mathematics 3A", "ECE 10AL", etc.
_DEPT_RE = re.compile(
    r'\b(' + '|'.join(DEPT_ALIASES.keys()) + r')\s+(\d+[A-Za-z]{0,4})\b',
    re.IGNORECASE,
)

# Matches a bare course number not preceded by a digit, e.g. the "2A" in "Math 3A or 2A"
_BARE_NUM_RE = re.compile(r'(?<!\d)\b(\d+[A-Za-z]{0,4})\b')

_CONCURRENT_RE = re.compile(
    r'(?:may be taken concurrently|concurrent(?:ly)?|concurrent enrollment)',
    re.IGNORECASE,
)

# Splits on "or" but NOT "or better / equivalent / higher / above / consent / permission"
_OR_SPLIT_RE = re.compile(
    r'\bor\b(?!\s+(?:better|equivalent|higher|above|consent|permission))',
    re.IGNORECASE,
)


def _normalize_dept(matched: str) -> str:
    """Map a matched department string to its canonical abbreviation."""
    for pattern, canonical in DEPT_ALIASES.items():
        if re.fullmatch(pattern, matched, re.IGNORECASE):
            return canonical
    return matched.upper().replace(" ", "")


def _extract_courses(alt: str, inherited_dept) -> tuple:
    """
    Extract course codes from one OR-alternative phrase.

    Handles:
      "CMPSC 16"      -> explicit dept + number
      "Math 3A or 2A" -> bare "2A" inherits the last-seen dept (MATH)

    Returns (list_of_codes, last_dept_seen).
    """
    codes = []
    last_dept = inherited_dept

    explicit = list(_DEPT_RE.finditer(alt))

    for m in explicit:
        dept = _normalize_dept(m.group(1))
        code = f"{dept} {m.group(2).upper()}"
        if code not in codes:
            codes.append(code)
        last_dept = dept

    # Track character spans already covered by explicit matches
    covered = set()
    for m in explicit:
        covered.update(range(m.start(), m.end()))

    # Pick up bare numbers not inside an explicit match
    for m in _BARE_NUM_RE.finditer(alt):
        if m.start() in covered:
            continue
        if last_dept:
            code = f"{last_dept} {m.group(1).upper()}"
            if code not in codes:
                codes.append(code)

    return codes, last_dept


def parse_prerequisites(raw: str) -> dict:
    """
    Parse a prerequisite string into structured data.

    Returns:
    {
      "required":    [ ["CMPSC 16"], ["MATH 3A", "MATH 2A"] ],
      "concurrent":  [ "MATH 3A" ],
      "raw":         "original text",
      "note":        "explanation of the format"
    }

    Structure of `required`:
      Each element is a GROUP (list of course codes).
      - A group with ONE entry    -> that course is strictly required.
      - A group with 2+ entries   -> OR/conditional: any one satisfies it.
      All groups must be satisfied (AND across groups).

    `concurrent` lists courses allowed to be taken at the same time.

    Parsing steps:
      1. Split on semicolons  -> top-level independent requirements
      2. Split each on commas (respecting parentheses) -> sub-clauses
      3. Split each sub-clause on "or" -> alternatives
      4. Bare numbers like "2A" in "Math 3A or 2A" inherit the preceding dept
      5. Sub-clauses with "(may be taken concurrently)" -> concurrent list
    """
    if not raw:
        return {"required": [], "concurrent": [], "raw": raw}

    # Step 1 — split on semicolons
    top_clauses = [c.strip() for c in re.split(r';', raw) if c.strip()]

    # Step 2 — split each on commas that are NOT inside parentheses
    clauses = []
    for tc in top_clauses:
        parts = re.split(r',\s*(?![^(]*\))', tc)
        clauses.extend(p.strip() for p in parts if p.strip())

    required_groups = []
    concurrent_courses = []

    for clause in clauses:
        is_concurrent = bool(_CONCURRENT_RE.search(clause))

        # Step 3 — split on "or" for alternatives
        alternatives = [a.strip() for a in _OR_SPLIT_RE.split(clause) if a.strip()]

        group_courses = []
        last_dept = None
        for alt in alternatives:
            # Step 4 — extract courses, inheriting dept for bare numbers
            codes, last_dept = _extract_courses(alt, last_dept)
            for code in codes:
                # Step 5 — route to concurrent or required
                target = concurrent_courses if is_concurrent else group_courses
                if code not in target:
                    target.append(code)

        if group_courses:
            required_groups.append(group_courses)

    return {
        "required":   required_groups,
        "concurrent": concurrent_courses,
        "raw":        raw.strip(),
        "note": (
            "Each item in 'required' is a prerequisite group. "
            "Within a group, any ONE course satisfies the requirement (OR/conditional). "
            "All groups must be satisfied together (AND). "
            "'concurrent' lists courses that may be taken at the same time."
        ),
    }


# ─────────────────────────────────────────────────────────────
# Phase 2 — Fetch and parse individual course pages
# ─────────────────────────────────────────────────────────────

def fetch_course_page(course_code: str) -> dict:
    """
    Fetch and parse a single course page from catalog.ucsb.edu.
    Returns a dict with course data, or None on failure.
    """
    url = f"{CATALOG_BASE}/{requests.utils.quote(course_code)}"
    try:
        r = _get(url)
    except requests.RequestException as e:
        print(f"  [WARN] Network error for {course_code}: {e}", file=sys.stderr)
        return None

    if r.status_code == 404:
        return None
    if r.status_code != 200:
        print(f"  [WARN] HTTP {r.status_code} for {course_code}", file=sys.stderr)
        return None

    text = r.text

    # Coursedog returns HTTP 200 even for unknown codes; real course pages
    # always contain an <h3> tag for at least one of these sections.
    if "<h3>" not in text and "Course Description" not in text:
        return None

    s = _parse_sections(text)

    full_title = _extract_title(text, course_code)

    # Combine all prerequisite-related fields for parsing
    prereq_raw  = s.get("Prerequisites", "")
    concurrent  = s.get("Concurrent Enrollment Requirements", "")
    coreq       = s.get("Corequisites", "")
    all_prereq  = " ".join(filter(None, [prereq_raw, concurrent, coreq]))

    return {
        "course_code":       course_code,
        "full_title":        full_title,
        "description":       s.get("Course Description", "").strip(),
        "units":             s.get("Unit Value", "").strip(),
        "prerequisites_raw": all_prereq.strip(),
        "prerequisites":     parse_prerequisites(all_prereq),
        "recommended_prep":  s.get("Recommended Preparation", "").strip(),
        "repeat_info":       s.get("Repeat Comments", "").strip(),
        "catalog_url":       url,
    }


# ─────────────────────────────────────────────────────────────
# Main orchestration
# ─────────────────────────────────────────────────────────────

def scrape_department(subject: str, dept_name: str, already_scraped: list, all_courses: dict) -> list:
    """
    Scrape all undergrad courses for one department.

    already_scraped : courses already loaded from a checkpoint for this dept
    all_courses     : the full in-progress dict (passed in for checkpointing)
    """
    print(f"\n{'='*60}")
    print(f"  {dept_name} ({subject})")
    print(f"{'='*60}")

    print("\n[Step 1] Discovering course codes from catalog listing pages...")
    course_codes = discover_courses_from_catalog_pages(subject)

    if not course_codes:
        print(f"  [ERROR] No courses found. Check discovery URLs.", file=sys.stderr)
        return already_scraped

    done_codes = {c["course_code"] for c in already_scraped}
    remaining  = [c for c in course_codes if c not in done_codes]

    print(f"  Total unique codes : {len(course_codes)}")
    if done_codes:
        print(f"  Already scraped    : {len(done_codes)}  (skipping)")
    print(f"  Remaining          : {len(remaining)}")

    results = list(already_scraped)

    print(f"\n[Step 2] Scraping {len(remaining)} course detail pages...")
    for i, code in enumerate(remaining, 1):
        idx = len(done_codes) + i
        print(f"  [{idx:3}/{len(course_codes)}] {code:<18}", end="", flush=True)

        course = fetch_course_page(code)

        if course is None:
            # All per-attempt retries exhausted — take a hard cooldown then
            # try once more before giving up on this course.
            print(f"--  (failed — cooling down {HARD_COOLDOWN}s)", flush=True)
            time.sleep(HARD_COOLDOWN)
            course = fetch_course_page(code)
            if course is None:
                print(f"  [{idx:3}/{len(course_codes)}] {code:<18}--  (skipped)", flush=True)
                continue

        results.append(course)
        print(f"OK  {(course['full_title'] or '(no title)')[:45]}")

        # Checkpoint after every successful scrape
        all_courses[subject] = results
        _save_checkpoint(all_courses)

    print(f"\n  Scraped {len(results)}/{len(course_codes)} courses successfully.")
    return results


def main():
    print("UCSB Course Catalog Scraper")
    print("Departments: MATH, CMPSC (CS), ECE")
    print("Discovery:   my.sa.ucsb.edu (plain-HTML course listing pages)")
    print("Details:     catalog.ucsb.edu (individual course pages)")
    print(f"Checkpoint:  {CHECKPOINT_FILE}  (auto-saved after every course)")
    print("Press Ctrl+C at any time to stop and save progress.\n")

    all_courses = _load_checkpoint()

    try:
        for subject, dept_name in DEPARTMENTS.items():
            existing = all_courses.get(subject, [])
            courses  = scrape_department(subject, dept_name, existing, all_courses)
            all_courses[subject] = courses

    except KeyboardInterrupt:
        total = sum(len(v) for v in all_courses.values())
        print(f"\n\n  Interrupted by user. {total} courses collected so far.")

    finally:
        # Always save on exit — whether finished, interrupted, or crashed
        _save_checkpoint(all_courses)
        _write_output(all_courses)
        total = sum(len(v) for v in all_courses.values())
        print(f"\n{'='*60}")
        print(f"  Done. {total} courses saved to '{OUTPUT_FILE}'")
        if os.path.exists(CHECKPOINT_FILE):
            print(f"  Checkpoint at '{CHECKPOINT_FILE}' — re-run to resume.")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()