"""The starting list, from the Reminder Planner spreadsheet (Nov 2026). Plain data only."""
import datetime

ROLES = (
    ("events_lead", "Events lead", "Staffing, prep and closeout for events.", "Zeke Chapman"),
    ("recruiting_lead", "Recruiting lead", "Customer positions, candidates and customer responses.", "Jason Collins"),
    ("payroll_approver", "Payroll approver", "Workdays, mileage and approving payroll.", "Zeke Chapman"),
    ("system_alerts", "System alerts", "Texting and email problems.", "Zeke Chapman"),
    ("billing", "Billing", "Payments that didn't go through.", ""),
)

T = datetime.time

# key, area, name, description, built_in, source, mode, days, business, repeat, send_time,
# (bell, email, text), recipients, backup, subject, message
RULES = (
    ("task_assigned", "Tasks", "Task assigned to you", "Know right away when someone gives you a task.", "tasks", "", "new", "", False, 0, T(8),
     (True, False, False), {}, "none", "", ""),
    ("task_due_soon", "Tasks", "Task due soon", "Morning of the day before it's due.", "tasks", "", "before", "1", False, 0, T(8),
     (True, False, False), {}, "none", "", ""),
    ("task_overdue", "Tasks", "Task overdue", "Next morning after the due date, then each weekday.", "tasks", "", "after", "1", False, 1, T(8),
     (True, False, False), {}, "none", "", ""),
    ("task_summary", "Tasks", "Morning summary", "Weekdays, early morning: what's due, overdue and waiting on you.", "tasks", "", "after", "", False, 1, T(7),
     (True, False, False), {}, "none", "", ""),
    ("event_short_staff", "Events", "Event short on staff", "7 days and 2 days before the event.", "events", "", "before", "7, 2", False, 0, T(8),
     (True, False, False), {"roles": ["events_lead"]}, "admins", "", ""),
    ("event_prep", "Events", "Event prep not finished", "3 days and 1 day before setup.", "events", "", "before", "3, 1", False, 0, T(8),
     (True, False, False), {"roles": ["events_lead"]}, "admins", "", ""),
    ("staff_requests", "Events", "Staff requests waiting", "Next morning summary, then daily.", "events", "", "new", "", False, 1, T(8),
     (True, False, False), {"roles": ["events_lead"]}, "admins", "", ""),
    ("event_closeout", "Events", "Event closeout not done", "3rd business day after the event, then weekdays.", "events", "", "after", "3", True, 1, T(8),
     (True, False, False), {"roles": ["events_lead"]}, "admins", "", ""),
    ("other_approvals", "Events", "Other approvals waiting", "Job postings, customer approvals, verifications.", "events", "", "new", "", False, 1, T(8),
     (True, False, False), {"roles": ["recruiting_lead"]}, "admins", "", ""),
    ("staff_working_soon", "Events", "You're working an event soon", "Where and when to show up.", "", "staff_working_soon", "before", "2, 1", False, 0, T(18),
     (False, False, True), {"related": ["staff"]}, "none", "Working {event}",
     "Hi {first_name}, reminder from Built to Work: you're working {event}. Be there {when} at {place}. Reply here with any questions."),
    ("event_changed", "Events", "Event changed", "Tell staff on the event when the date, time or place changes.", "", "event_changed", "changed", "", False, 0, T(8),
     (False, False, True), {"related": ["staff"]}, "none", "{event} changed",
     "Hi {first_name}, an update from Built to Work: {event} is now {when} at {place}. Reply here with any questions."),
    ("ticket_confirmation", "Participants", "Ticket and sign-up confirmation", "Participant gets their ticket right after signing up.", "tickets", "", "new", "", False, 0, T(8),
     (False, True, True), {}, "none", "", ""),
    ("participant_event_reminder", "Participants", "Event reminder for registered participants", "Day before the event, for people who said yes to texts.", "", "participant_event", "before", "1", False, 0, T(16),
     (False, False, True), {"related": ["participant"]}, "none", "See you at {event}",
     "Hi {first_name}, see you tomorrow at {event}! It starts {when} at {place}. Reply STOP to stop texts."),
    ("follow_up_due", "Participants", "Follow-up date reached", "Morning of the follow-up date.", "", "follow_up", "before", "0", False, 0, T(8),
     (True, False, False), {"related": ["owner"]}, "admins", "Follow up with {name} today", "{line}"),
    ("text_reply_waiting", "Participants", "Text reply not handled", "Still marked New after 1 business day.", "", "text_reply", "after", "1", True, 0, T(9),
     (True, False, False), {"related": ["owner"]}, "admins", "Text from {name} still needs an answer", "{line}"),
    ("missing_info", "Participants", "Missing info still not sent", "3 days after asking, then weekly.", "", "missing_info", "after", "3", False, 7, T(8),
     (True, False, False), {"related": ["owner"]}, "admins", "{name} still hasn't sent what you asked for", "{line}"),
    ("new_interest", "Participants", "New interest to review", "Same day someone shows interest in a position.", "", "new_interest", "new", "", False, 0, T(8),
     (True, False, False), {"related": ["recruiter"]}, "admins", "New interest from {name}", "{line}"),
    ("customer_no_response", "Recruiting", "Customer hasn't responded to candidates", "5 business days after sending.", "", "customer_no_response", "after", "5", True, 0, T(8),
     (True, False, False), {"roles": ["recruiting_lead"]}, "admins", "{company} hasn't responded about {name}", "{line}"),
    ("employer_feedback", "Recruiting", "Interview or feedback request", "Same day, then daily until someone logs a note or call.", "", "employer_feedback", "new", "", False, 1, T(8),
     (True, False, False), {"roles": ["recruiting_lead"]}, "admins", "{company} is waiting on you about {name}", "{line}"),
    ("new_position", "Recruiting", "New position submitted by a customer", "Same day.", "", "new_position", "new", "", False, 0, T(8),
     (True, True, False), {"roles": ["recruiting_lead"]}, "admins", "New position from {company}: {position}", "{line}. Review it in the app."),
    ("missing_workdays", "Payroll", "Workdays or mileage missing", "2 days before the pay week ends (Friday).", "", "missing_workdays", "before", "2", False, 0, T(9),
     (True, False, False), {"roles": ["payroll_approver"]}, "admins", "Missing workdays: {name}", "{line}"),
    ("payroll_ready", "Payroll", "Payroll ready to approve", "When it's ready, then daily.", "", "payroll_ready", "new", "", False, 1, T(8),
     (True, True, False), {"roles": ["payroll_approver"]}, "admins", "Payroll ready to approve: {period}", "{line}. Approve it in the app."),
    ("pay_statement", "Payroll", "Your pay statement is ready", "Link-only email when payroll is approved.", "statements", "", "new", "", False, 0, T(8),
     (False, True, False), {}, "none", "", ""),
    ("onboarding_submitted", "Customers", "Onboarding form ready to review", "When a customer submits their onboarding form, then daily until the customer is created.",
     "", "onboarding_submitted", "new", "", False, 1, T(9), (True, True, False), {"related": ["owner"]}, "admins",
     "{company} finished their onboarding form", "{line}. Review it and press Create customer."),
    ("delivery_behind", "Recruiting", "Position behind on candidates", "Paid-for positions still short 14 days after the charge, then weekly.",
     "", "delivery_behind", "after", "14", False, 7, T(9), (True, False, False), {"related": ["recruiter"], "roles": ["recruiting_lead"]}, "admins",
     "{position} for {company}: {remaining} still owed", "{line}"),
    ("document_expiring", "Customers", "Customer document expiring", "Like an insurance certificate. 30 and 7 days before it expires.",
     "", "documents_expiring", "before", "30, 7", False, 0, T(9), (True, True, False), {"related": ["account_manager"]}, "admins",
     "{document} for {company} expires {end_date}", "{line}. Ask the customer for an updated copy."),
    ("agreement_ending", "Billing", "Monthly agreement ending", "Customer and account manager, before the end date. Nothing renews until an Admin or Manager renews it.",
     "", "agreement_ending", "before", "30, 7", False, 0, T(9), (True, True, False), {"related": ["customer_contact", "account_manager"]}, "admins",
     "Your Built to Work agreement ends {end_date}",
     "Hello,\n\nYour monthly Built to Work agreement for {company} ({amount} a month) ends on {end_date}. It does not renew on its own. {manager} will be in touch about renewing.\n\nThank you,\nBuilt to Work"),
    ("billing_problem", "Billing", "Payment not collected", "A position fee or monthly payment failed or is waiting on a bank account.",
     "", "billing_problem", "new", "", False, 2, T(9), (True, True, False), {"related": ["account_manager"], "roles": ["billing"]}, "admins",
     "Payment not collected: {company}", "{line}. Check the customer's Billing tab."),
    ("connection_failing", "Admin", "Texting or email sign-in stopped working", "As soon as it fails.", "", "connection_failing", "new", "", False, 0, T(8),
     (True, True, True), {"roles": ["system_alerts"]}, "admins", "Action needed: {connection} isn't working", "{line}. Fix it in Settings → Connections."),
    ("messages_failing", "Admin", "Messages failing to send", "Daily summary at 5 PM when anything failed.", "", "messages_failing", "after", "0", False, 0, T(17),
     (True, True, True), {"roles": ["system_alerts"]}, "admins", "{count} messages didn't go out today", "Built to Work: {details}. Check Settings → Connections."),
)


def apply(ReminderRole, ReminderRule, User=None):
    for position, (key, name, description, waiting) in enumerate(ROLES):
        ReminderRole.objects.get_or_create(key=key, defaults={"name": name, "description": description,
                                                              "waiting_for": waiting, "position": position})
    for position, row in enumerate(RULES):
        (key, area, name, description, built_in, source, mode, days, business, repeat, send_time,
         (bell, email, text), recipients, backup, subject, message) = row
        ReminderRule.objects.get_or_create(key=key, defaults=dict(
            area=area, name=name, description=description, built_in=built_in, source=source, mode=mode, days=days,
            business_days=business, repeat_every=repeat, send_time=send_time, bell=bell, email=email, text=text,
            recipients=recipients, backup=backup, subject=subject, message=message, position=position * 10))
