"""Starting questions and a placeholder agreement. Staff edit both in Settings → Customer onboarding."""

TRADES = "\n".join(("Electrician", "Plumber", "HVAC technician", "Welder", "Carpenter", "Heavy equipment operator",
                    "CDL driver", "Maintenance technician", "Manufacturing / production", "Warehouse / logistics",
                    "General labor", "Other"))

QUESTIONS = (
    ("work", "What kind of work does your company do?", "A few sentences about your business and the projects or products people work on.", "long", "", True),
    ("work", "Which trades or roles do you hire for most?", "", "multi", TRADES, True),
    ("work", "Where are most of the jobs located?", "Cities, counties or regions.", "short", "", False),
    ("work", "About how many people do you expect to hire in the next 12 months?", "", "choice", "1–5\n6–20\n21–50\nMore than 50", False),
    ("work", "What schedules do your workers usually have?", "For example: day shift, nights, rotating, overtime, travel.", "short", "", False),
    ("process", "Walk us through your hiring process, from application to first day.", "", "long", "", True),
    ("process", "Who makes the final hiring decision?", "Name and title.", "short", "", False),
    ("process", "What do you require before someone starts?", "", "multi",
     "Drug screen\nBackground check\nDriving record (MVR)\nPhysical\nSkills test\nInterview\nNone of these", False),
    ("process", "How quickly do you usually decide after an interview?", "", "choice",
     "Same day\nWithin 2–3 days\nWithin a week\nLonger than a week", False),
    ("people", "Required qualifications (must-haves)", "Licenses, certifications, years of experience or skills someone must have to be considered.", "long", "", True),
    ("people", "Standards (what you expect every day)", "Things like attendance, safety, attitude, reliable transportation, following instructions.", "long", "", True),
    ("people", "Preferred, but not required", "Things that would make someone stand out.", "long", "", False),
    ("people", "What makes someone a great fit at your company?", "", "long", "", False),
    ("people", "Anything that would rule someone out?", "", "long", "", False),
)

AGREEMENT = """DRAFT — REPLACE WITH BUILT TO WORK'S SERVICE AGREEMENT BEFORE SENDING TO CUSTOMERS.

This placeholder is here so the signing step can be tested. An administrator can paste the real agreement in Settings → Customer onboarding → Service agreement. Each change is saved as a new version, and every customer's signature is stored with the exact text they agreed to.
"""


def apply(Question, Agreement):
    if not Question.objects.exists():
        for i, (section, label, help_text, kind, options, required) in enumerate(QUESTIONS):
            Question.objects.create(section=section, label=label, help_text=help_text, kind=kind, options=options,
                                    required=required, position=(i + 1) * 10)
    if not Agreement.objects.exists():
        Agreement.objects.create(body=AGREEMENT, version=1, is_active=True)
