"""
build_viz_json.py
-----------------
Reads ucsb_courses.json (output of ucsb_course_scraper.py), re-parses every
course through the Course class and parse_prerequisites(), and writes a
clean visualization-ready JSON file: ucsb_courses_viz.json.

The output schema is a flat list of course objects, each shaped like:

{
  "id":          "MATH 4B",          -- unique node id (== course code)
  "code":        "MATH 4B",
  "title":       "Differential Equations",
  "department":  "MATH",
  "units":       4,
  "description": "First and second order differential equations...",
  "prereqs": {
    "required":    [["MATH 4A", "MATH 4AI"]],   -- AND of OR-groups
    "concurrent":  []
  },
  "catalog_url": "https://catalog.ucsb.edu/courses/MATH%204B"
}

Usage
-----
    python build_viz_json.py                          # default paths
    python build_viz_json.py courses.json out.json   # custom paths
"""

import json
import sys
import re
from prereq_parser import parse_prerequisites


# ── Course class (matches the schema from course.py) ─────────────────────────

class Course:
    def __init__(self):
        self.code        = ""
        self.title       = ""
        self.department  = ""
        self.units       = 0
        self.description = ""
        self.prereqs     = ([], [])   # (required_groups, concurrent_groups)
        self.catalog_url = ""


def _course_from_dict(data: dict, department: str) -> Course:
    c = Course()
    c.code        = data.get("course_code", "")
    c.title       = data.get("full_title", "")
    c.department  = department
    c.catalog_url = data.get("catalog_url", "")

    # Units: scraper stores as a string; use 0 as fallback for variable units
    raw_units = data.get("units", "")
    try:
        c.units = int(raw_units)
    except (ValueError, TypeError):
        c.units = 0

    # Description: strip trailing "Units Fixed" / "Units Variable" noise
    desc = data.get("description", "")
    desc = re.sub(r'\s*Units\s+(Fixed|Variable|Range)\s*$', '', desc,
                  flags=re.IGNORECASE).strip()
    c.description = desc

    # Prerequisites: re-parse from the raw string so we get the normalized,
    # abbreviated form (e.g. "MATHEMATICS" -> "MATH", "CS" -> "CMPSC")
    prereq_raw = data.get("prerequisites_raw", "")
    c.prereqs = parse_prerequisites(prereq_raw)

    return c


def _course_to_dict(c: Course) -> dict:
    required, concurrent = c.prereqs
    return {
        "id":          c.code,
        "code":        c.code,
        "title":       c.title,
        "department":  c.department,
        "units":       c.units,
        "description": c.description,
        "prereqs": {
            "required":   required,
            "concurrent": concurrent,
        },
        "catalog_url": c.catalog_url,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def build(input_path: str, output_path: str) -> None:
    with open(input_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # The scraper nests courses under a "courses" key; fall back to treating
    # the whole file as the dept map if that key is absent.
    dept_map = raw.get("courses", raw)

    courses = []
    skipped = 0

    for department, course_list in dept_map.items():
        for data in course_list:
            # Skip ghost entries (deprecated / does-not-exist courses)
            if (not data.get("full_title")
                    and not data.get("description")
                    and not data.get("recommended_prep")):
                skipped += 1
                continue

            c = _course_from_dict(data, department)

            # Skip courses where we couldn't recover even a code
            if not c.code:
                skipped += 1
                continue

            courses.append(_course_to_dict(c))

    # Sort: by department first, then numerically by course number
    def _sort_key(d):
        m = re.match(r'^[A-Z]+\s+(\d+)([A-Z]*)', d["code"])
        return (d["department"], int(m.group(1)) if m else 0, m.group(2) if m else "")

    courses.sort(key=_sort_key)

    payload = {
        "meta": {
            "departments": sorted({c["department"] for c in courses}),
            "total_courses": len(courses),
            "description": (
                "Visualization-ready course graph data. "
                "Each course is a node; prerequisite edges are encoded in "
                "prereqs.required (AND-of-OR-groups) and prereqs.concurrent."
            ),
        },
        "courses": courses,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Done: {len(courses)} courses written to '{output_path}'"
          + (f"  ({skipped} skipped)" if skipped else ""))


if __name__ == "__main__":
    input_path  = sys.argv[1] if len(sys.argv) > 1 else "ucsb_courses.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "ucsb_courses_viz.json"
    build(input_path, output_path)