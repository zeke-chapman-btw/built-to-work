# Reminders (Settings → Reminders)

Managers and administrators set up reminders here. Nothing goes out until someone presses **Turn on reminders**.

- **List:** every reminder from the Reminder Planner spreadsheet, grouped by area. Click one to change it. Rows marked
  "Runs elsewhere" (task reminders, the event reminders from the Tasks build, ticket confirmations, pay statement emails)
  are set where the note says.
- **Who gets it:** any mix of people connected to the item (staff on the event, the person who set the follow-up,
  the recruiter…), roles, and specific people. If nobody can be found, it goes to administrators (or no one, for
  texts to staff and participants).
- **Roles:** Events lead, Recruiting lead, Payroll approver, System alerts. "Not in the app yet" holds a name:
  administrators get those reminders until a staff account with that first and last name exists, then it switches
  to them automatically.
- **Channels:** Bell (Inbox → Reminders until the new bell is finished), Email (noreply@ from Connections),
  Text (EZ Texting; 8 AM–9 PM Eastern only, never to numbers that replied STOP). Several items for one person become one list.
- **+ New reminder:** pick what to watch, then timing, people, channels and wording. New kinds of data need a new
  entry in `apps/reminders/sources.py`.

The message worker checks every 5 minutes (`apps/messaging/runtime.py`). Each send is recorded once in
`ReminderSend`, so restarts never repeat a reminder.
