# Build 18A: task reminders

The existing Task, TaskNotification, bell, Notifications page and Home Recent activity remain the source and delivery surfaces. No email, SMS, escalation, payroll, recruiting-stage or approval reminders are introduced.

## Staff controls

- Settings → Your account → Reminders: optional activity preference; administrators also see company settings and authorized managers see event settings.
- Task → Reminders: one hour / next applicable morning snooze; event tasks also offer an optional weekend work date. This changes neither the due date nor task status.
- Notifications: existing read controls plus snooze; summaries link to current authorized tasks.

Task deadlines are date-only. A timestamp is still rejected by the existing form. `weekend_work_date` is an explicit Saturday/Sunday work date, independent of the main event's start. A primary event, or exactly one additional event link, supplies the event timezone. Ambiguous links cannot establish weekend eligibility. There is no defined team relationship, so no manager broadcast is added.

## Schedule

| Content | Recipient | Time |
| --- | --- | --- |
| Assignment | Current assignee | Existing audit notification, once; includes self-assignment when enabled |
| Advance deadline | Current assignee | Previous Monday–Friday day, 7 a.m. |
| Due deadline | Current assignee | Due date, 7 a.m., on an applicable workday |
| Overdue | Current assignee | One current morning summary; no repeated direct overdue alerts |
| Activity / completion | Current creator or assignee with access and optional updates enabled | Since the previous summary |

General summaries run Monday–Friday at 7 a.m. `America/New_York`. Eastern event tasks share that weekday summary. Other event scopes run at 7 a.m. in the stored event timezone. Weekend scopes contain only explicitly scheduled weekend event work, including setup before a later main event. No holiday calendar or text inference is used.

Monday's activity window includes weekend changes since the preceding summary. Unique audit receipts prevent repeated activity. Deadline coverage is independent of general/event scope and weekend metadata, so moving a task does not repeat an unchanged deadline. Each task appears once within a summary. No empty notification is created.

## Operation

`gunicorn.conf.py` is discovered by Gunicorn from the repository working directory. `post_worker_init` starts a bounded scheduler loop on the existing Preview web service; `worker_exit` stops it. The start command, service plan and Blueprint are unchanged. No new service is needed.

Automatic operation requires all of:

- Approved Preview service `srv-db8n044s728c73aaet00`.
- Preview gate enabled.
- Locmem email backend.
- `BTW_TASK_REMINDERS_ENABLED`, derived from the same Preview environment checks.

Other Render services, including Production, remain disabled. An isolated local review can instead enable the setting with `DEBUG=True`, no Render service ID, and `BTW_TASK_REMINDERS_LOCAL_WORKER=true`. The local interval defaults to 60 seconds; `BTW_TASK_REMINDERS_LOCAL_INTERVAL` can shorten it for review. The opted-in local SQLite worker configures `IMMEDIATE` transactions before request threads start so concurrent writes honor the busy timeout. PostgreSQL settings are unchanged.

Inspect successful automatic operation without delivering a manual tick:

```sh
python manage.py run_task_reminders --status
```

`last_automatic_at` advances only after a successful automatic tick. Manual runs cannot update it. `automatic_healthy` requires a recent successful automatic run and no outstanding reported error. Startup logs include `TASK_REMINDER_WORKER_STARTED`.

For bounded recovery only:

```sh
python manage.py run_task_reminders --max-jobs 100
```

A manual command is not activation or proof of automatic operation.

## Reliability and data

Five additive models hold company/event policy, personal preferences and snoozes, durable jobs, unique coverage items, and scheduler health. Migration `0010_task_reminders_18a` adds these and nullable notification links plus the optional task work date. Existing task ownership, deadlines, audit records and notification read state are preserved; there is no historical backfill or relaxed history safeguard.

PostgreSQL uses short `SKIP LOCKED` claims with expiring tokens. Delivery then locks events, tasks, staff profiles, recipient and job in source-compatible order, and commits coverage plus the existing notification atomically. Contended profile/user rows retry, yielding to existing staff-account edits. Expired claims recover; token checks reject stale workers. Failures retain bounded execution history and retry with backoff. Source edits reconcile after commit; a bounded rotating scan repairs missed callbacks and independent account/profile changes.

After downtime, only a useful current-day summary is planned for each applicable recipient/scope. Obsolete deadline jobs are suppressed instead of replaying every missed day. Current ownership, status, permissions, policy and snooze are checked before generation and display. Rendering batches relationship lookups and resolves current authorized task labels; it does not store private task titles in reminder messages.

System execution never fabricates human task edits. Settings and human weekend-date changes use explicit audit services. Existing source-task audit notifications are reused rather than duplicated.

## Verification

```sh
python manage.py test --settings=config.settings.test
node --test scripts/*.cjs
```

Run Django checks on both isolated SQLite and PostgreSQL databases. Use a short PostgreSQL base/test database name. The Build 18A tests include real PostgreSQL concurrent claiming, competing summary/deadline coverage, stale claims, clock boundaries, role boundaries, state changes, preferences and read state. Browser and operational evidence belongs in `outputs/build18a-task-reminders/`.

References: [Gunicorn configuration](https://gunicorn.org/configure/), [Gunicorn lifecycle settings](https://gunicorn.org/reference/settings/), [Render paid service behavior](https://render.com/docs/faq), [Django SQLite transaction modes](https://docs.djangoproject.com/en/5.2/ref/databases/).
