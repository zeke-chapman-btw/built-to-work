"""Starting guides. A migration adds any that aren't in the app yet; after that, admins edit them in Settings → Training."""

STAFF = ["event_staff", "recruiter", "manager", "admin"]

GUIDES = [
    dict(slug="find-your-way-around", title="Find your way around", position=10, minutes=2, audiences=STAFF,
         summary="The menu, My stuff, the bell and Help.",
         steps=[
             dict(title="Use the menu on the left", page="/my/schedule/", target=".workspace-nav",
                  body="Everything you can use is in the menu on the left. You only see the parts your job needs.\n"
                       "On a phone, tap ☰ at the top of the screen to open the menu."),
             dict(title="Open My stuff", page="/my/schedule/", target="text:My stuff",
                  body="My stuff is your own corner of the app: your schedule, the days you've worked, your training "
                       "and your account."),
             dict(title="Check the bell", page="/my/schedule/", target="[data-notification-bell] summary",
                  body="The bell at the top right shows reminders and updates for you. A number on it means something new."),
             dict(title="Get help any time", page="/my/schedule/", target="text:Help",
                  body="Help has quick answers. Your step-by-step guides are in My stuff → Training, and any guide can "
                       "walk you through the task on screen."),
         ]),
    dict(slug="ask-to-work-an-event", title="Ask to work an event", position=20, minutes=2, audiences=["event_staff"],
         summary="Pick an open event, choose your days and send the request.",
         steps=[
             dict(title="Open My stuff", page="/my/schedule/", target="text:My stuff",
                  body="Open events, on the right, lists the events that still need staff."),
             dict(title="Press Request next to the event", page="/my/schedule/", target="text:Request",
                  image="es-open-events.jpg",
                  body="Press Request beside the event you'd like to work. You'll see its dates and the sign-up deadline."),
             dict(title="Tick the days you can work", target="input[name=dates]", image="es-request-days.jpg",
                  body="Tick every day you can work. Setup and travel days show here too when the event has them."),
             dict(title="Add a note if you need to", target="textarea[name=note]",
                  body="Anything a manager should know, like \"I can only stay until 3 on Saturday\". This is optional."),
             dict(title="Send the request", target="text:Send request", image="es-request-send.jpg",
                  body="Press Send request. A manager approves or declines it. You'll see the answer under My requests, "
                       "and the event moves to Coming up once you're booked."),
         ]),
    dict(slug="your-schedule", title="See your schedule and event details", position=30, minutes=2, audiences=["event_staff"],
         summary="Where to be, when to arrive, housing and travel notes.",
         steps=[
             dict(title="Look at Coming up", page="/my/schedule/", target="text:Coming up||.ops-list-card",
                  body="Coming up lists every event you're booked for: your days, when to arrive, the address with a map "
                       "link, and housing if it's been booked."),
             dict(title="Open an event for everything else", page="/my/schedule/", target=".my-event-title a",
                  body="Press an event's name to see all of its details, including the on-site contact."),
             dict(title="Read the travel and setup notes", page="/my/schedule/", target=".my-travel summary",
                  body="If the event has travel or setup instructions, press Travel & setup notes to open them."),
             dict(title="Keep an eye on this pay week", page="/my/schedule/", target=".my-week",
                  body="The box at the top counts the days you've worked this pay week. Press it to see each day."),
         ]),
    dict(slug="check-in-at-an-event", title="Check in at an event", position=40, minutes=2, audiences=["event_staff"],
         summary="Check in each day you work so your day and mileage are counted.",
         steps=[
             dict(title="On the day, open My stuff", page="/my/schedule/", target=".my-today||text:My schedule",
                  body="On a day you're booked, a TODAY card shows at the top of My stuff."),
             dict(title="Press Check in", target="text:Check in",
                  body="Press Check in on the TODAY card, or open the event and press Check in at the top."),
             dict(title="Pick your role and whether you drove", target="input[name=role]",
                  body="Choose your role for the day, and say whether you drove your own vehicle. If you drove, the "
                       "mileage for the round trip is added for you."),
             dict(title="Press Check in for today", target="text:Check in for today",
                  body="That's it. If you miss a day or pick the wrong role, ask a manager to fix it."),
             dict(title="See your days worked", page="/workdays/mine/", target="text:Days worked",
                  image="es-days-worked.jpg",
                  body="Every day you've checked in shows under My stuff → Days worked."),
         ]),
    dict(slug="update-your-account", title="Update your details and password", position=50, minutes=2, audiences=STAFF,
         summary="Your name, phone, shirt size, password and two-step sign-in.",
         steps=[
             dict(title="Open My account", page="/my/account/", target="text:My account",
                  body="My stuff → My account has your details."),
             dict(title="Fix your details and press Save", page="/my/account/", target="text:Save",
                  image="es-account.jpg",
                  body="Update your name, email, phone or shirt size, then press Save."),
             dict(title="Change your password or two-step sign-in", page="/my/account/", target="text:Sign-in||text:Change password",
                  body="Further down, Sign-in lets you change your password and turn on two-step sign-in."),
             dict(title="Choose how reminders reach you", page="/my/account/", target="text:My notifications",
                  body="Press My notifications to pick bell, email or text for each reminder you get."),
         ]),
]
