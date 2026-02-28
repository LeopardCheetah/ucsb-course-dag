"""
UCSB Course Catalog Scraper
============================
Scrapes all undergraduate MATH, CMPSC (CS), and ECE courses from:
  https://catalog.ucsb.edu/courses/DEPT%20NUMBER

Strategy:
  1. Phase 1 - Discover course IDs: Query the public UCSB Curriculum API
     (no API key required) to get a full list of course codes per department.
  2. Phase 2 - Scrape details: Fetch each course's catalog page (server-side
     rendered HTML) and parse title, description, units, and prerequisites.

Prerequisite parsing:
  - Concurrent prereqs  : "(may be taken concurrently)" → flagged as concurrent=True
  - Conditional prereqs : "or" between course codes    → stored as a list of alternatives
  - Sequential prereqs  : "and" / "with a grade of"   → stored as required courses

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

CATALOG_BASE   = "https://catalog.ucsb.edu/courses"
SCHEDULE_API   = "https://api.ucsb.edu/academics/curriculums/v3/classes/search"
# Most recent available quarter (Winter 2025 = 20252; use a recent one)
# We try multiple quarters so we catch every active course.
QUARTERS       = ["20254", "20251", "20244", "20241"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (research/course-catalog-scraper)",
    "Accept": "text/html,application/xhtml+xml",
}

REQUEST_DELAY  = 0.4   # seconds between requests — be polite
REQUEST_TIMEOUT = 15


# ─────────────────────────────────────────────────────────────
# Phase 1 – Discover course codes via UCSB Curriculum API
# ─────────────────────────────────────────────────────────────

def fetch_course_ids_from_api(subject: str) -> set[str]:
    """
    Query the UCSB public curriculum API (no key required) to list all
    course codes for a given subject across several recent quarters.
    Returns a set of strings like {"CMPSC 16", "CMPSC 24", ...}
    """
    found = set()
    for quarter in QUARTERS:
        page = 1
        while True:
            params = {
                "quarter":     quarter,
                "subjectCode": subject,
                "pageSize":    100,
                "pageNumber":  page,
            }
            try:
                r = requests.get(SCHEDULE_API, params=params,
                                 headers=HEADERS, timeout=REQUEST_TIMEOUT)
                if r.status_code == 401:
                    # API requires a key — fall back to range scan
                    return found
                if r.status_code != 200:
                    break
                data = r.json()
                classes = data.get("classes", [])
                if not classes:
                    break
                for c in classes:
                    code = f"{c.get('subjectArea', subject).strip()} {c.get('catalogNumber', '').strip()}"
                    found.add(code.strip())
                # Pagination
                total = data.get("total", 0)
                if page * 100 >= total:
                    break
                page += 1
                time.sleep(REQUEST_DELAY)
            except Exception:
                break
    return found


def build_course_ids_range_scan(subject: str) -> list[str]:
    """
    Fallback: generate candidate course IDs by scanning common UCSB
    course number patterns.  UCSB uses:
      - plain integers:   1–299
      - letter suffixes:  16, 24A, 130A, 10AL
    We generate the common ones and rely on HTTP 404 / empty-page detection
    to skip non-existent ones.
    """
    candidates = []
    suffixes = ["", "A", "B", "C", "D", "L", "AL", "BL", "CL"]
    for n in range(1, 300):
        for s in suffixes:
            candidates.append(f"{subject} {n}{s}")
    return candidates


# ─────────────────────────────────────────────────────────────
# Phase 2 – Scrape individual course pages
# ─────────────────────────────────────────────────────────────

class CoursePageParser(HTMLParser):
    """
    Lightweight parser that extracts labeled sections from Coursedog
    server-rendered course pages.

    The page structure is:
      <h3>Section Label</h3>
      <p>Content</p>   (or sometimes just text nodes after the h3)
    """

    def __init__(self):
        super().__init__()
        self.data: dict[str, str] = {}
        self._current_label: str | None = None
        self._capture        = False
        self._depth          = 0
        self._buf            = []
        self._in_h3          = False
        self._h3_buf         = []

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self._in_h3 = True
            self._h3_buf = []
        elif tag in ("p", "div", "span") and self._current_label:
            self._capture = True
            self._buf = []
        elif tag == "h2":
            # h2 resets section (new major block)
            self._current_label = None
            self._capture = False

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_h3 = False
            label = "".join(self._h3_buf).strip()
            if label:
                self._current_label = label
                self._capture = False
                self._buf = []
        elif tag in ("p", "div", "span") and self._capture:
            text = "".join(self._buf).strip()
            if text and self._current_label:
                existing = self.data.get(self._current_label, "")
                self.data[self._current_label] = (existing + " " + text).strip()
            self._capture = False

    def handle_data(self, data):
        if self._in_h3:
            self._h3_buf.append(data)
        elif self._capture:
            self._buf.append(data)


def fetch_course_page(dept: str, course_code: str) -> dict | None:
    """
    Fetch and parse a single course page.
    Returns a dict with course data, or None if the page doesn't exist.
    course_code examples: "CMPSC 16", "MATH 4A", "ECE 10AL"
    """
    url_code = requests.utils.quote(course_code)
    url = f"{CATALOG_BASE}/{url_code}"
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

    # Quick check: if the page title says "Courses | University of" and
    # there's no course-specific h1, the course doesn't exist
    # (Coursedog returns 200 with the courses listing page for unknown codes)
    if f"# {course_code.replace(' ', '')}" not in html and \
       f"## {dept}" not in html and \
       "Course Description" not in html:
        return None

    # ── Parse with HTMLParser ──
    parser = CoursePageParser()
    parser.feed(html)
    sections = parser.data

    # ── Extract key fields from HTML directly ──
    full_title   = _extract_between(html, "##", "\n", after=f"## {course_code} - ")
    description  = sections.get("Course Description", "")
    units        = sections.get("Unit Value", "")
    prereq_raw   = sections.get("Prerequisites", "")
    concurrent   = sections.get("Concurrent Enrollment Requirements", "")
    coreq        = sections.get("Corequisites", "")
    recommended  = sections.get("Recommended Preparation", "")
    repeat       = sections.get("Repeat Comments", "")

    # Alternative title extraction if above failed
    if not full_title:
        m = re.search(r'##\s+' + re.escape(course_code) + r'\s+-\s+(.+)', html)
        full_title = m.group(1).strip() if m else ""

    # Combine all prereq-like text
    all_prereq_text = " ".join(filter(None, [prereq_raw, concurrent, coreq]))

    return {
        "course_code":       course_code,
        "full_title":        full_title,
        "department":        dept,
        "description":       description.strip(),
        "units":             units.strip(),
        "prerequisites_raw": all_prereq_text.strip(),
        "prerequisites":     parse_prerequisites(all_prereq_text),
        "recommended_prep":  recommended.strip(),
        "repeat_info":       repeat.strip(),
        "catalog_url":       url,
    }


def _extract_between(text: str, start_marker: str, end_marker: str,
                     after: str = "") -> str:
    """Utility: extract text between two markers, optionally after a prefix."""
    search_in = text
    if after:
        idx = text.find(after)
        if idx == -1:
            return ""
        search_in = text[idx + len(after):]
    end_idx = search_in.find(end_marker)
    if end_idx == -1:
        return search_in[:200].strip()
    return search_in[:end_idx].strip()


# ─────────────────────────────────────────────────────────────
# Prerequisite Parsing
# ─────────────────────────────────────────────────────────────

# Matches course codes like "CMPSC 16", "Math 3A", "ECE 10AL", "PSTAT 120A"
COURSE_CODE_RE = re.compile(
    r'\b([A-Z][A-Za-z\s]{1,12}?)\s+(\d+[A-Za-z]{0,3})\b'
)

# Detects concurrent phrasing
CONCURRENT_RE = re.compile(
    r'(?:may be taken concurrently|concurrent(?:ly)?|concurrent enrollment)',
    re.IGNORECASE
)

# Splits on "or" to get alternatives (but not "or better", "or equivalent")
OR_SPLIT_RE = re.compile(r'\bor\b(?!\s+better)(?!\s+equivalent)', re.IGNORECASE)


def parse_prerequisites(raw: str) -> dict:
    """
    Parse a prerequisite string into structured data.

    Returns:
    {
      "required":    [["CMPSC 16"], ["MATH 3A", "MATH 2A"]],  # AND of groups
      "concurrent":  ["MATH 3A"],                              # may be taken concurrently
      "raw":         "original text"
    }

    Each element of `required` is a GROUP.
    - A group with one item  → that course is strictly required.
    - A group with 2+ items  → you need ONE of these (OR / conditional prereq).
    """
    if not raw:
        return {"required": [], "concurrent": [], "raw": raw}

    # Split top-level by semicolons and commas that separate major clauses
    # (we try to keep "or" alternatives together)
    # Strategy: split on semicolons first, then handle each clause
    clauses = re.split(r';', raw)

    required_groups: list[list[str]] = []
    concurrent_courses: list[str] = []

    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue

        is_concurrent = bool(CONCURRENT_RE.search(clause))

        # Split on "or" to find alternatives in this clause
        alternatives = [a.strip() for a in OR_SPLIT_RE.split(clause) if a.strip()]

        group_courses = []
        for alt in alternatives:
            # Extract course codes from this alternative
            codes = COURSE_CODE_RE.findall(alt)
            for dept_part, num_part in codes:
                dept_part = dept_part.strip()
                code = f"{dept_part} {num_part}"
                if is_concurrent:
                    if code not in concurrent_courses:
                        concurrent_courses.append(code)
                else:
                    if code not in group_courses:
                        group_courses.append(code)

        if group_courses:
            # Each group represents a set of OR alternatives for one requirement
            required_groups.append(group_courses)

    return {
        "required":   required_groups,
        "concurrent": concurrent_courses,
        "raw":        raw.strip(),
        "note": (
            "Each item in 'required' is a prerequisite group. "
            "Within a group, any one course satisfies the requirement (OR). "
            "Groups themselves are all required (AND). "
            "'concurrent' lists courses that may be taken at the same time."
        )
    }


# ─────────────────────────────────────────────────────────────
# Main orchestration
# ─────────────────────────────────────────────────────────────

def discover_courses(subject: str) -> list[str]:
    """
    Returns a deduplicated, sorted list of course code strings for a department.
    Tries API first; falls back to range scan + existence check.
    """
    print(f"\n[{subject}] Discovering course codes...")
    codes = fetch_course_ids_from_api(subject)
    if codes:
        print(f"  → Found {len(codes)} codes via UCSB Curriculum API.")
        return sorted(codes)

    # Fallback: range scan (slower)
    print(f"  → API unavailable or returned no data. Using range scan...")
    candidates = build_course_ids_range_scan(subject)
    confirmed = []
    for c in candidates:
        url_code = requests.utils.quote(c)
        url = f"{CATALOG_BASE}/{url_code}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            time.sleep(REQUEST_DELAY)
            if r.status_code == 200 and "Course Description" in r.text:
                confirmed.append(c)
                print(f"    Found: {c}")
        except Exception:
            pass
    return sorted(confirmed)


def scrape_department(subject: str, dept_name: str) -> list[dict]:
    """Discover and scrape all courses for a single department."""
    course_codes = discover_courses(subject)
    if not course_codes:
        print(f"  [WARN] No courses found for {subject}. Exiting department.")
        return []

    results = []
    total = len(course_codes)
    print(f"\n[{subject}] Scraping {total} courses...")

    for i, code in enumerate(course_codes, 1):
        print(f"  [{i}/{total}] {code} ...", end=" ", flush=True)
        course = fetch_course_page(subject, code)
        if course:
            results.append(course)
            print(f"✓ {course['full_title'][:50]}")
        else:
            print("(not found / skipped)")

    print(f"\n[{subject}] Done. Scraped {len(results)} courses.")
    return results


def main():
    all_courses: dict[str, list[dict]] = {}
    total_scraped = 0

    for subject, dept_name in DEPARTMENTS.items():
        courses = scrape_department(subject, dept_name)
        all_courses[subject] = courses
        total_scraped += len(courses)

    # ── Save to JSON ──
    output = {
        "source":      "https://catalog.ucsb.edu",
        "departments": list(DEPARTMENTS.keys()),
        "total_courses": total_scraped,
        "prerequisite_format": {
            "description": (
                "Each course has a 'prerequisites' object with three fields: "
                "'required', 'concurrent', and 'raw'. "
                "'required' is a list of GROUPS. Each group is a list of course codes. "
                "Within a group the courses are OR alternatives (you only need one). "
                "Across groups the requirements are AND (you need one from each group). "
                "'concurrent' lists courses that may be taken at the same time as this course. "
                "'raw' is the original unmodified prerequisite text."
            ),
            "example": {
                "required":   [["MATH 3A", "MATH 2A"], ["CMPSC 8"]],
                "concurrent": ["MATH 3A"],
                "raw":        "Math 3A or 2A with grade C or better (may be taken concurrently), CMPSC 8"
            }
        },
        "courses": all_courses,
    }

    outfile = "ucsb_courses.json"
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅  Saved {total_scraped} courses to {outfile}")
    print(f"    Departments: {list(DEPARTMENTS.keys())}")


if __name__ == "__main__":
    main()