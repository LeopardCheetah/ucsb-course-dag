"""
UCSB Course Catalog Scraper
============================
Scrapes all undergraduate MATH, CMPSC (CS), and ECE courses.

Course Discovery (the fix):
  Uses the UCSB General Catalog department pages at my.sa.ucsb.edu — these
  render plain HTML with every real course listed by name.  We extract course
  codes from these pages as the ground-truth list, so we never request a
  course that doesn't actually exist.

Course Details:
  Fetches each course's individual page on catalog.ucsb.edu (server-side
  rendered) and parses title, description, units, and prerequisites.

Prerequisite parsing:
  - Concurrent prereqs  : "(may be taken concurrently)" → flagged as concurrent
  - Conditional prereqs : "or" between course codes     → stored as OR groups
  - Required prereqs    : "and" / sequential            → each is its own group

Output: ucsb_courses.json
"""

import requests
import json
import time
import re
import sys
from html.parser import HTMLParser

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
DEPARTMENTS = {
    "MATH":  "Mathematics",
    "CMPSC": "Computer Science",
    "ECE":   "Electrical and Computer Engineering",
}

# Old UCSB General Catalog — plain HTML, no JS required.
# These pages list every real course for each department.
# We try multiple years so discontinued/new courses are both captured.
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

CATALOG_BASE    = "https://catalog.ucsb.edu/courses"
REQUEST_DELAY   = 0.4   # seconds between requests — be polite
REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (research/course-catalog-scraper)",
    "Accept": "text/html,application/xhtml+xml",
}

# Matches "MATH 3A", "CMPSC 16", "ECE 10AL", etc. in the discovery HTML.
COURSE_CODE_PATTERNS = {
    "MATH":  re.compile(r'\bMATH\s+(\d+[A-Z]{0,4})\b'),
    "CMPSC": re.compile(r'\bCMPSC\s+(\d+[A-Z]{0,6})\b'),
    "ECE":   re.compile(r'\bECE\s+(\d+[A-Z]{0,4})\b'),
}

# Only include undergraduate courses (course number strictly below this)
UNDERGRAD_MAX_COURSE_NUMBER = 200


# ─────────────────────────────────────────────────────────────
# Phase 1 — Discover real course codes from the old catalog
# ─────────────────────────────────────────────────────────────

def discover_courses_from_catalog_pages(subject: str) -> list[str]:
    """
    Fetch the department's course listing page(s) from the old plain-HTML
    UCSB catalog and extract every course code mentioned there.
    This is the authoritative ground-truth list — no false positives.
    """
    pattern = COURSE_CODE_PATTERNS[subject]
    found: set[str] = set()

    for url in DISCOVERY_URLS[subject]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            time.sleep(REQUEST_DELAY)
            if r.status_code != 200:
                print(f"  [WARN] Discovery page returned HTTP {r.status_code}: {url}",
                      file=sys.stderr)
                continue

            numbers = pattern.findall(r.text)
            before = len(found)
            for num in numbers:
                course_num = int(re.match(r'\d+', num).group())
                if course_num < UNDERGRAD_MAX_COURSE_NUMBER:
                    found.add(f"{subject} {num}")
            added = len(found) - before
            skipped = len(numbers) - added
            suffix = f"  (skipped {skipped} grad courses >= {UNDERGRAD_MAX_COURSE_NUMBER})" if skipped else ""
            print(f"  + {added} undergrad codes from: {url}{suffix}")

        except requests.RequestException as e:
            print(f"  [WARN] Could not reach discovery page: {e}", file=sys.stderr)

    if not found:
        print(f"  [WARN] No courses discovered for {subject}. "
              "Check that the discovery URLs are reachable.", file=sys.stderr)

    return sorted(found, key=lambda c: _course_sort_key(c, subject))


def _course_sort_key(code: str, subject: str) -> tuple:
    """Sort courses numerically: MATH 3A < MATH 4A < MATH 34B < MATH 100A."""
    rest = code[len(subject):].strip()
    m = re.match(r'^(\d+)([A-Z]*)$', rest)
    if m:
        return (int(m.group(1)), m.group(2))
    return (9999, rest)


# ─────────────────────────────────────────────────────────────
# Phase 2 — Scrape individual course pages
# ─────────────────────────────────────────────────────────────

class SectionParser(HTMLParser):
    """
    Parses the Coursedog-rendered course page HTML.
    Extracts labeled sections (h3 heading → following paragraph text).
    """
    def __init__(self):
        super().__init__()
        self.sections: dict[str, str] = {}
        self._label: str | None = None
        self._in_h3 = False
        self._h3_buf: list[str] = []
        self._capture = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self._in_h3 = True
            self._h3_buf = []
        elif tag == "h2":
            self._label = None
            self._capture = False
        elif tag in ("p", "div", "span") and self._label:
            self._capture = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_h3 = False
            label = "".join(self._h3_buf).strip()
            if label:
                self._label = label
                self._capture = False
        elif tag in ("p", "div", "span") and self._capture:
            text = "".join(self._buf).strip()
            if text and self._label:
                existing = self.sections.get(self._label, "")
                self.sections[self._label] = (existing + " " + text).strip()
            self._capture = False

    def handle_data(self, data):
        if self._in_h3:
            self._h3_buf.append(data)
        elif self._capture:
            self._buf.append(data)


def fetch_course_page(course_code: str) -> dict | None:
    """
    Fetch and parse a single course page from catalog.ucsb.edu.
    Returns a dict with course data, or None on failure.
    """
    url = f"{CATALOG_BASE}/{requests.utils.quote(course_code)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        time.sleep(REQUEST_DELAY)
    except requests.RequestException as e:
        print(f"  [WARN] Network error for {course_code}: {e}", file=sys.stderr)
        return None

    if r.status_code == 404:
        return None
    if r.status_code != 200:
        print(f"  [WARN] HTTP {r.status_code} for {course_code}", file=sys.stderr)
        return None

    html = r.text

    # Coursedog returns 200 even for unknown course codes, but a real course
    # page always contains at least one of these markers.
    if "Course Description" not in html and "Full Course Title" not in html:
        return None

    # Parse sections
    parser = SectionParser()
    parser.feed(html)
    s = parser.sections

    # Extract full title from the "## DEPT N - Title" heading
    m = re.search(r'##\s+' + re.escape(course_code) + r'\s+-\s+(.+)', html)
    full_title = m.group(1).strip() if m else s.get("Full Course Title", "")

    # Combine all prereq-flavored fields into one string for parsing
    prereq_raw = s.get("Prerequisites", "")
    concurrent = s.get("Concurrent Enrollment Requirements", "")
    coreq      = s.get("Corequisites", "")
    all_prereq = " ".join(filter(None, [prereq_raw, concurrent, coreq]))

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
# Prerequisite Parsing
# ─────────────────────────────────────────────────────────────

# Maps long/alternative department names to canonical abbreviations
DEPT_ALIASES: dict[str, str] = {
    r'(?:Computer\s+Science|CS|CMPSC)':         'CMPSC',
    r'(?:Math(?:ematics)?)':                    'MATH',
    r'(?:ECE|Electrical(?:\s+(?:and\s+)?Computer\s+Engineering)?)': 'ECE',
    r'(?:PHYS|Physics)':                        'PHYS',
    r'(?:PSTAT|Statistics(?:\s+&\s+Applied\s+Probability)?)': 'PSTAT',
    r'(?:ENGR|Engineering(?:\s+Sciences)?)':    'ENGR',
    r'(?:CHEM|Chemistry(?:\s+and\s+Biochemistry)?)': 'CHEM',
}

_DEPT_RE = re.compile(
    r'\b(' + '|'.join(DEPT_ALIASES.keys()) + r')\s+(\d+[A-Za-z]{0,4})\b',
    re.IGNORECASE,
)

CONCURRENT_RE = re.compile(
    r'(?:may be taken concurrently|concurrent(?:ly)?|concurrent enrollment)',
    re.IGNORECASE,
)

# Splits on "or" but NOT "or better / equivalent / higher / above / consent"
OR_SPLIT_RE = re.compile(
    r'\bor\b(?!\s+(?:better|equivalent|higher|above|consent|permission))',
    re.IGNORECASE,
)


def _normalize_dept(matched: str) -> str:
    for pattern, canonical in DEPT_ALIASES.items():
        if re.fullmatch(pattern, matched, re.IGNORECASE):
            return canonical
    return matched.upper().replace(" ", "")


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
      - A group with ONE entry  → that course is strictly required.
      - A group with MULTIPLE entries → OR/conditional: any one satisfies it.
      All groups must be satisfied (AND logic across groups).

    `concurrent` lists courses explicitly permitted to be taken simultaneously.
    """
    if not raw:
        return {"required": [], "concurrent": [], "raw": raw}

    # Semicolons separate independent requirements
    clauses = [c.strip() for c in re.split(r';', raw) if c.strip()]

    required_groups: list[list[str]] = []
    concurrent_courses: list[str] = []

    for clause in clauses:
        is_concurrent = bool(CONCURRENT_RE.search(clause))

        # Split on "or" to find alternatives within this clause
        alternatives = [a.strip() for a in OR_SPLIT_RE.split(clause) if a.strip()]

        group_courses: list[str] = []
        for alt in alternatives:
            for match in _DEPT_RE.finditer(alt):
                dept_raw, number = match.group(1), match.group(2)
                dept = _normalize_dept(dept_raw)
                code = f"{dept} {number.upper()}"
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
# Main orchestration
# ─────────────────────────────────────────────────────────────

def scrape_department(subject: str, dept_name: str) -> list[dict]:
    print(f"\n{'='*60}")
    print(f"  {dept_name} ({subject})")
    print(f"{'='*60}")

    print(f"\n[Step 1] Discovering course codes from catalog listing pages...")
    course_codes = discover_courses_from_catalog_pages(subject)

    if not course_codes:
        print(f"  [ERROR] No courses found. Check discovery URLs.", file=sys.stderr)
        return []

    print(f"  Total unique codes: {len(course_codes)}")

    print(f"\n[Step 2] Scraping {len(course_codes)} course detail pages...")
    results = []
    for i, code in enumerate(course_codes, 1):
        print(f"  [{i:3}/{len(course_codes)}] {code:<18}", end="", flush=True)
        course = fetch_course_page(code)
        if course:
            results.append(course)
            title_preview = course["full_title"][:45] or "(no title)"
            print(f"OK  {title_preview}")
        else:
            print("--  (not found on catalog.ucsb.edu)")

    print(f"\n  Scraped {len(results)}/{len(course_codes)} courses successfully.")
    return results


def main():
    print("UCSB Course Catalog Scraper")
    print("Departments: MATH, CMPSC (CS), ECE")
    print("Discovery: my.sa.ucsb.edu (plain-HTML course listing pages)")
    print("Details:   catalog.ucsb.edu (individual course pages)\n")

    all_courses: dict[str, list[dict]] = {}
    total_scraped = 0

    for subject, dept_name in DEPARTMENTS.items():
        courses = scrape_department(subject, dept_name)
        all_courses[subject] = courses
        total_scraped += len(courses)

    output = {
        "source":           "https://catalog.ucsb.edu",
        "discovery_source": "https://my.sa.ucsb.edu/catalog (plain-HTML dept pages)",
        "departments":      list(DEPARTMENTS.keys()),
        "total_courses":    total_scraped,
        "prerequisite_format": {
            "description": (
                "Each course has a 'prerequisites' object with three keys: "
                "'required', 'concurrent', and 'raw'. "
                "'required' is a list of groups. Within each group, any ONE course "
                "satisfies the requirement (OR/conditional prereqs). All groups "
                "must be satisfied (AND). "
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

    outfile = "ucsb_courses.json"
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"  Saved {total_scraped} courses to '{outfile}'")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()