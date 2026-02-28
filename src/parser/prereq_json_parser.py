import json

class Course:
    code = '' # course code ~ e.g. ece 5
    title = '' # course title ~ e.g. Advanced Linear Algebra (or something)
    description = '' # full course description
    units = 0

    # if a prereq for a class is:
    # - A and 
    # - B and 
    # - C or D or E and
    # - concurrent enrollment in F and G
    # 
    # then the course prereq would look like 
    # ([[A], [B], [C, D, E]], [[F, G]])
    # wlog this should only be at most 2 layers deep
    # empty list => no prereqs/concurrent reqs
    prerequisites = ([], [])

    # allowed_majors = [] - not that important; might implement later
    
    def __init__(self, course_code = '', course_title = '', course_description = '', num_units = 0, prerequisites = ([], [])):
        self.code = course_code
        self.title = course_title
        self.description = course_description
        self.units = num_units
        self.prerequisites = prerequisites


    def __str__(self):
        return f"""
        Course Code: {self.code}
        Course Name: {self.title}
        Course Description: {self.description}
        Course Units: {self.units}
        Course Prerequisites: {self.prerequisites}

        """




# parse class list 
def parse_json():

    import prereq_parser as pparser

    courses = []

    ###################################################


    fp = "cleaned_ucsb_courses.json"

    lines = [] # a workable json object
    with open(fp, 'r') as f:
        lines = json.load(f)
    
    for k in lines.keys(): # k = {math, cmpsc, ece}
        ls = lines[k][:]
        for c in ls:
            # check if course exists lowkey 
            if c["full_title"] == "" and c["description"] == "" and c["recommended_prep"] == "":
                continue # course is deprecated / DNE

            course = Course()
            course.code = c["course_code"]
            course.title = c["full_title"]
            course.description = c["description"]
            
            
            if course.description[-12:].lower() == " Units Fixed".lower():
                course.description = course.description[:-12]
            
            if course.description[-15:].lower() == " Units Variable".lower():
                course.description = course.description[:-15]


            if c["units"] == "":
                course.units = 0.5 # dummy representing variable units
            else:
                course.units = int(c["units"])

            # TODO - parse c[prerequisites_raw] into a form above
            course.prerequisites = pparser.parse_prerequisites(c["prerequisites_raw"].replace("Either", "").replace("either", ""))


            courses.append(course)

'''
    s = set()
    for c in courses:
        s.add(c.code)

    for c in courses:
        p = c.prerequisites
        for ls in p[0] + p[1]:
            for i in ls:
                if i not in s:
                    print(i)
'''
    



if __name__ == "__main__":
    parse_json()


