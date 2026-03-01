# UCSB Course DAG

This visualization was made to visualize UCSB's Math/CS/ECE class pre-reqs! This project was almost entirely vibecoded and was made in 3 days with Claude (although the READMEs were written w/o AI assistance).

This visualization is not complete, and was made for visual/aesthetic purposes. Don't use this application to plan your course schedule.

----

## Setup

#### Online

- Navigate to this website's [`github pages`](https://leopardcheetah.github.io/ucsb-course-dag/final_course_dag.html) and click the `Load Visualization!` button.
- Profit.

#### Local

(Note: The only use case I can think for locally hosting this site is if you want to upload our own `courses.json` file to this visualization. In that case, follow the steps below.)

- Download this repo into some directory `%DIR%`. 
- Navigate to `%DIR%/` and run `python -m http.server 8000`
- Go to [`localhost:8000/src/viz/viz11.html`](http://localhost:8000/src/viz/viz11.html) and upload your desired `.json` file (`ucsb_courses_info.json` for UCSB Math/CS/ECE classes) file where it says "Drop Course Json here."
- Profit.

----

## Notes

- Click/hover over a class to find more about it and what its prereqs are!
- Not all buttons on the visualization work. The demo classes button is also kind of useless.
- It's **highly recommended** to click the `Hide Special` and `Hide External` buttons near the top left and to collapse the bottom bar of "isolated" classes to avoid clutter and clear out useless classes (special = Special Topics classes, External = non CS/ECE/Math classes (e.g. Phys 7a)).
- Note that not all classes are present in the visualiation in a meaningful way. For example, ECE 5 is missing from the big graph since it has no pre-reqs and doesn't fulfill any pre-reqs despite it being a major requirement for CE/EE majors. 
- **Not all information present in the `ucsb_courses_info.json` file and visualization is accurate** and up to date (at the very least, the course prereqs for ECE 152A were represented incorrectly). I'm confident there are no AI Hallucinations in the data, but the parsing might not work[^1].

---


[^1]: In particular: if the course requirements are more than 2 "layers" deep (e.g. the prereq for this course is (course A or (course B and course C))), that requirement cannot be represented correctly by my system.