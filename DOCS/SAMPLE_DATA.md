# Local screen-review samples

With a migrated **local** database and the project's dependencies installed:

```sh
python manage.py load_sample_data --settings=config.settings.development
```

The command runs on demand. It is not part of a build, migration, deployment,
or server startup. A second run leaves the existing dataset and passwords
unchanged. It creates:

- 25 fictional people, including **SAMPLE Avery Johnson** (busy) and
  **SAMPLE Empty Profile**, with a mix of contact information and work preferences.
- Skills, work history, education/training, licenses/certificates, verification
  decisions, internal notes, four touchpoints, and a follow-up task due today.
- Four events dated in the past, today, and the future; staffing plans,
  assignments, participant registrations/trailer participation, and staff check-ins.
- Two customers with contacts and approved job needs, plus job connections in
  active and closed stages.
- Trailer skills tests (including a completed, subsequently voided test),
  simulator captures, game results, a nonofficial practice run, and an imported
  past score. The practice run follows the app's rule that nonofficial runs do
  not belong to a person.
- Two small fictional PDF documents on the busy profile.

Business names and narrative fields begin with SAMPLE. Email addresses use
example.test and phone numbers use the reserved 555-01xx range. Existing
configuration such as permission names and recruiting stage labels is reused.
The loader uses domain services for intake, profile preferences, notes, work
history, staffing, check-in, customer needs, candidate selection/transitions,
interactions/tasks, trailer progression, scoring, and historical import. Where
there is no creation service, it validates the model and calls record_audit
explicitly. It never fabricates aggregate reporting totals.

The terminal prints generated passwords **once**, for these usernames:

| Username | Access |
| --- | --- |
| SAMPLE_admin | Administrator; linked staff profile |
| SAMPLE_event_lead | Manager and Event Staff; lead assignments |
| SAMPLE_staff | Recruiting and Event Staff; document viewing |
| SAMPLE_staff_no_docs | Recruiting and Event Staff; no document access |

Passwords are stored only as normal Django password hashes. They are not saved
in source files, manifests, or audit payloads. Keep the terminal private. Intake
invitations are suppressed and ticket delivery opt-ins remain off; email is
also redirected to the in-memory backend during loading.

To remove the dataset without recreating it:

```sh
python manage.py load_sample_data --reset --settings=config.settings.development
```

Reset uses exact model names, primary keys, and storage filenames from an audit
manifest. It preserves other data, including unrelated records named SAMPLE.
It refuses the entire reset if new, unowned business records reference the
dataset. The service audit trail and reset receipt remain. Reset never guesses
ownership from a name prefix or deletes a directory of files.

The migration adds one narrow exception to the database's hiring-history delete
guard: the revision must have a SAMPLE title **and** its exact primary key must
appear in the latest active sample manifest. All other history guards remain.

Production settings are always refused, even with DEBUG enabled or
`--allow-preview`. Non-production settings with DEBUG disabled require that
explicit flag. Remote PostgreSQL and hosted environments require it as well.
Running against the live Preview database still requires owner approval.

Targeted verification:

```sh
python manage.py test apps.core.test_sample_data apps.participants.test_intake_submission_11b --settings=config.settings.test
```

Use a short PostgreSQL TEST.NAME in the local harness (for example
`test_btw_sample_` plus a 12-character job hash). Do not derive a database name
from a long worktree path or full test label.
