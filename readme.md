# UCSB Course DAG

This visualization was made to help visualize the Math/CS/ECE class pre-reqs! This project was almost entirely vibecoded and was made in 3 days with Claude (although the READMEs were written w/o AI assistance).

This visualization is not complete, and was made for visual/aesthetic purposes. Don't use this application to plan your course schedule.

----

## Setup

- Download [`final_course_dag.html`](final_course_dag.html) and [`ucsb_courses_info.json`](ucsb_courses_info.json) into some directory `%DIR%`. 
- Navigate to `%DIR%/` and run `python -m http.server 8000`
- Go to [`localhost:8000/final_course_dag.html`](http://localhost:8000/final_course_dag.html) and upload the `ucsb_courses_info.json` file where it says "Drop Course Json here."
- Profit

----

## Notes

- To my knowledge, all information present in the `ucsb_courses_info.json` file is accurate and up to date (e.g. there are no AI Hallucinations of classes that don't exist).
- Click/hover over a class to find more about it and what its prereqs are!
- Not all buttons on the visualization work. 
- It's **highly recommended** to click the `Hide Special` and `Hide External` buttons near the top left and to collapse the bottom bar of "isolated" classes to avoid clutter and clear out useless classes (special = Special Topics classes, External = non CS/ECE/Math classes (e.g. Phys 7a)).
- Note that not all classes are present in the visualiation in a meaningful way. For example, ECE 5 is missing from the big graph since it has no pre-reqs and doesn't fulfill any pre-reqs despite it being a major requirement for CE/EE majors. 