"""Starting text for the in-app Workflow handbook (Help → Handbook).

Each section is shown only to the roles in `audiences`. Admins edit sections in
Settings → Training → Handbook; this file only seeds sections that don't exist yet.

Writing format (kept simple so anyone can edit it):
  - a line starting with "- " is a bullet; indent it 2 spaces to nest it under the line above
  - a line starting with "1. " (any number) is a numbered step
  - "### Heading" is a small heading
  - **words** are bold; a blank line starts a new paragraph
"""

ALL = ["admin", "manager", "recruiter", "event_staff"]
RECRUITING = ["admin", "manager", "recruiter"]
MANAGING = ["admin", "manager"]

HANDBOOK = [
    dict(slug="whos-who", position=10, audiences=ALL, title="Who's who",
         summary="The kinds of accounts in the app and what each one does.",
         guides=["find-your-way-around"],
         body="""- **Admin:** everything, including settings, staff, reminders, sign-up forms and training.
- **Manager:** events, staffing, customers, approvals, positions and recruiting. Can view both portals.
- **Recruiter:** participants, touchpoints, texts and emails, and connecting people to positions. Can view the participant portal.
- **Event staff:** their schedule, asking to work events, and checking in at events.
- **Participant:** a job seeker. Uses the participant portal.
- **Customer:** an employer. Uses the customer portal. Their company lead manages their own team.

This handbook shows you the parts for your role. Step-by-step click-throughs are in Help → Step-by-step guides, and each section links to the ones that match."""),

    dict(slug="working-an-event", position=20, audiences=ALL, title="Working an event",
         summary="Asking to work, your schedule, and checking in on the day.",
         guides=["ask-to-work-an-event", "your-schedule", "check-in-at-an-event"],
         body="""1. **Ask to work it:** events that are open for staff show under My stuff → Schedule. Pick your days and send the request. A manager approves or declines it.
2. **Check your schedule:** My stuff → Schedule shows where to be, arrival times, housing, travel notes and contacts.
3. **Reminder texts:** you get one two days before and the evening before an event you're working, and another if the date, time or place changes.
4. **On the day:** check in for each day you work. Pick your role and whether you drove. Mileage is worked out for you.
5. **At the event:** you can see sign-ups and participants, look up phone numbers and re-send someone's ticket."""),

    dict(slug="your-days-and-pay", position=30, audiences=ALL, title="Your days and pay",
         summary="How your days worked turn into pay.",
         guides=["your-schedule"],
         body="""- Your days worked come from your event check-ins.
- If My pay is turned on for you, My stuff → My pay shows each week's pay stub and your year-to-date totals.
- Missing a day or mileage? Tell your manager before payroll is approved so they can add it.
- You get an email when a new pay statement is ready."""),

    dict(slug="people-coming-in", position=100, audiences=RECRUITING, title="People coming in",
         summary="How job seekers get into the app and become ready to recruit.",
         guides=["add-a-participant", "import-participants", "find-a-participant"],
         body="""**Goal:** every job seeker ends up as one participant record that's ready to recruit.

1. **At an event (most people):** they sign up at the trailer on the event's sign-up form. That records their details, the event, and the terms they agreed to. Results from stations attach to their record.
2. **Online:** they sign up on the website form, which sends it to the app. "How they found us" shows Online.
3. **Added by staff:** Participants → + Add participant, for someone you met another way.
4. **Imports:** admins bring in spreadsheets. Older event sign-ups and the old dashboard were brought in this way. Imported people keep the event they came from, and the consent they gave at that event counts as a completed sign-up.
5. **Duplicates:** the app matches on email and phone and flags possible duplicates so staff can merge them.
6. **Ready to recruit:** a person can be put forward to customers once their sign-up and consent are on record. If something's missing, their recruiting page says what.

**Notifications:** the "New participant" reminder tells chosen staff when someone new comes in. It can be limited, for example to certain events, trades or areas."""),

    dict(slug="working-with-participants", position=110, audiences=RECRUITING, title="Working with participants",
         summary="Finding people, keeping in touch, and keeping their record complete.",
         guides=["read-a-profile", "log-a-touchpoint", "text-or-email-someone", "work-your-inbox", "follow-ups", "upload-a-document"],
         body="""1. **Find them:** Participants, with search and filters (trade interest, event, how they found us, area and more).
2. **Read the profile:** overview, work history, documents, Culture Index, qualifications, job connections and touchpoints.
3. **Log a touchpoint** for every call, text, email or meeting, and set a follow-up date if there's more to do.
4. **Text or email** from the profile. Replies land in the Inbox (Mine / Unassigned). Mark them handled when done. People who reply STOP can't be texted until they reply START.
5. **Follow-ups:** Participants → Follow-ups lists everyone you promised to get back to, soonest first.
6. **Documents:** upload from the profile (Documents → Manage). Anything BTW staff upload is marked verified automatically."""),

    dict(slug="positions-and-hires", position=120, audiences=RECRUITING, title="Positions, recruiting and hires",
         summary="From a customer's job request to a confirmed hire.",
         guides=["add-a-position", "approvals", "connect-a-candidate"],
         body="""**Goal:** fill a customer's job with the right participant, and record the hire.

1. **Position comes in:** the customer requests it in their portal, or staff add it (customer → + New position). The customer can tick "Post this job for Built to Work participants?"
2. **BTW is told:** the customer's BTW recruiter gets the "New position" reminder (or the Recruiting lead if none is set).
3. **Approve:** a manager or admin presses **Approve** or **Send back for changes**. On the approve screen:
  - Pick the fee (skilled, general labor or no charge). The customer's bank is charged.
  - Tick "Post this job for participants" to put it on the participant job board. The screen shows what participants will see. No company name is shown.
4. **Recruiting starts automatically:** approving sets up recruiting, owned by the customer's BTW recruiter, and posts it if chosen. It shows under Recruiting → Positions.
5. **Find candidates:** on the position, press + Add candidate for suggested matches. Participants can also show interest in posted jobs.
6. **Add to position:** review a match → **Add to position**. Nothing is shared with the customer yet.
7. **Work the pipeline:** contact them, log touchpoints, and change the stage as things happen.
8. **Share with customer:** on the candidate, press **Share with customer**. You can preview what the customer sees first. The customer gets an email and sees the candidate in their portal.
9. **Customer updates the status:** the customer runs the interviews and updates the status: Interested → Interview scheduled → Interviewed → Offer made → Hired, or Not a fit at any time, with an optional date and note.
  - BTW's stage follows along automatically and never moves backwards.
  - The recruiter gets a reminder each time, until someone logs a touchpoint.
  - "Not a fit" doesn't close the candidate out. The recruiter decides and changes the stage.
10. **Confirm the hire:** when the customer marks someone Hired, the recruiter checks it and presses **Confirm hire**. Only BTW can make a hire official.
11. **Work history:** the job is added to the participant's work history as "Placed by Built to Work", with the company, position and start date.

**Other reminders:** customer hasn't responded to a shared candidate, and paid-for positions still short on candidates."""),

    dict(slug="participant-side", position=130, audiences=RECRUITING, title="What participants see",
         summary="The participant portal.",
         guides=[],
         body="""- **Home:** their upcoming events, jobs and next steps.
- **Events:** see upcoming events and sign up.
- **Jobs:** see posted jobs and say they're interested. BTW sees the interest on the position.
- **Profile:** update contact details, work history and documents.
- **Help:** participant guides.

To see it the way they do: Settings → Portals."""),

    dict(slug="customer-side", position=140, audiences=RECRUITING, title="What customers see",
         summary="The customer portal.",
         guides=[],
         body="""- **Dashboard:** candidates waiting on them, open positions, interviews and hires.
- **Positions:** request a new one (with the option to post it for participants), and see its status or changes requested.
- **Candidates:** review who BTW shared and keep the status updated, with a date and note. They can also log touchpoints with BTW.
- **Company & team:** company details and who at their company can use the portal.
- **Billing:** bank setup and charges.
- **Help:** customer guides.

Admins and managers can see it with practice data: Settings → Portals."""),

    dict(slug="running-events", position=200, audiences=MANAGING, title="Running events",
         summary="Create an event, staff it, and close it out.",
         guides=["create-an-event", "staff-an-event", "close-out-an-event"],
         body="""1. **Create:** Events → + New event. Add the date, place, purpose and stations, then fill in staff, schedule, housing and sign-up from its page.
2. **Publish to staff:** staff can then see it and ask to work it.
3. **Staff it:** approve work requests in Approvals, or add people directly, with a role for each day.
4. **Event day:** staff check in from their phone. Participants sign up at the trailer and go through stations.
5. **Close out:** check the staff check-ins (fix missed ones), add expenses, look over the report, then More → Mark complete.

**Notifications:** reminders for upcoming events, staffing gaps and events not closed out. Events marked "Managed outside of this app" (still run in Base44) get no reminders or to-dos."""),

    dict(slug="customers", position=210, audiences=MANAGING, title="Customers, start to finish",
         summary="Onboarding, their BTW recruiter, and portal access.",
         guides=["onboard-a-customer", "customer-portal-access"],
         body="""1. **Start onboarding:** Customers → Onboarding → + Onboard customer. Enter the company name and the contact's email.
2. **Fill it in or send it:** go through the steps yourself (Company, Contacts, Work & hiring, W-9 & agreement), or use ••• → Email the link so the customer fills it in.
3. **Review and create:** when it's submitted, press Review → Create customer.
4. **Pick their BTW recruiter:** on the customer, Edit → Built to Work recruiter. New positions from this customer go to that person, and they run the recruiting. If nobody is picked, the Recruiting lead gets them.
5. **Portal access:** Plan & portal → Send invitation. The contact sets a password. Their company lead can then add their own team.
6. **Ongoing:** notes, touchpoints, positions, campaigns and billing all live on the customer's page."""),

    dict(slug="billing", position=220, audiences=MANAGING, title="Billing",
         summary="Position fees and how customers are charged.",
         guides=["add-a-position"],
         body="""1. **Bank setup:** the customer connects a bank account in their portal (Billing), through Stripe.
2. **Fees are set at approval:** skilled positions are charged in sets of 3 candidates, general labor per person needed, or no charge.
3. **Charging:** if the bank is ready, the charge goes through right after approval. If not, it waits and charges once the bank is added.
4. **Changes:** approving an updated version defaults to no new charge. Pick a fee only if they're adding candidates or people.
5. **Track it:** the customer's Billing tab shows each charge, monthly agreements, and candidates paid for vs delivered. Monthly agreements never renew on their own."""),

    dict(slug="payroll", position=230, audiences=MANAGING, title="Payroll",
         summary="Checking days worked and approving pay.",
         guides=["close-out-an-event"],
         body="""1. Staff days come from event check-ins. Fix missed check-ins when you close out the event.
2. Before the pay week ends, a reminder flags missing workdays or mileage.
3. The payroll approver reviews and approves. A reminder goes out when payroll is ready.
4. Staff get an email when their pay statement is ready."""),

    dict(slug="reminders", position=240, audiences=ALL, title="Reminders and notifications",
         summary="Who hears about what, and how.",
         guides=["reminders-setup"],
         body="""- **The bell and Inbox:** reminders meant for you show in the bell and your Inbox.
- **Your choices:** under My stuff you can choose how you hear about things (email, text, bell or reminder) where that's allowed.
- **Who gets what:** each reminder goes to a role (like Recruiting lead), to the person tied to the item (like the recruiter on a position), or to named people. If nobody is set, admins get it.
- **Going forward only:** turning a reminder on doesn't send alerts for old items.
- **Admins:** Settings → Reminders lists every reminder. Change the timing, who gets it and how, or add your own."""),

    dict(slug="admin-setup", position=300, audiences=["admin"], title="Admin and setup",
         summary="Staff, forms, training and connections.",
         guides=["add-staff", "sign-up-forms", "training-setup", "reminders-setup"],
         body="""- **Staff:** Settings → Staff → Add staff. Choose Create a sign-in, tick their roles (Manager, Recruiter, Event staff) and any reminder roles they cover, and email them a link to set their own password.
- **Sign-up forms:** edit the event and online sign-up forms. Changes go live when published.
- **Training and this handbook:** Settings → Training. Edit guides, add videos, and edit handbook sections and who sees each one.
- **Sign-in messages:** Settings → Sign-in messages, a pop-up shown on next sign-in, by account type.
- **Email signatures:** each person sets theirs. The company logo is added automatically.
- **Connections:** Stripe (billing), Mailchimp (marketing), email and text. Keys are entered on the app's Connections page, never shared in chat."""),
]
