# Home Dashboard Phase 2A: Quick Actions

Quick Actions Edit opens a staff-only editor with nine ordered slots. Choose No action or Remove to clear a slot; Up/Down swaps adjacent slots. Save returns to Home; Restore Defaults clears the saved selection. The form also works without JavaScript by choosing actions directly in numbered slots.

Selections are stored in accounts.User.quick_action_ids (nullable JSON). NULL means current permission-filtered defaults; [] means intentionally empty. Only stable catalog IDs are saved. Every request filters by current permissions; POST validates choices, duplicates and the nine-slot limit. URLs are resolved from trusted named routes. Existing destination authorization remains in force. CSRF protection applies to Save and Restore.

Administrators control catalog availability through existing Django user/group permissions in the existing admin interface. No new roles, button configuration or URL entry is required. Personal settings is available to all active staff.

| ID | Action | Existing permission (any listed) | Default |
| --- | --- | --- | --- |
| create-event | Create event | events.add_event | Yes |
| participant-directory | Participant directory | participants.view_participant | Yes |
| events | Events | events.view_event | Yes |
| qualifications | Qualifications | participants.view_participantqualification | Yes |
| documents | Documents | participants.view_participantdocument | Yes |
| operations | Operations dashboard | events.view_syncoutboxitem | Yes |
| quizzes | Quizzes and tests | assessments.view_assessmentcategory, assessments.add_assessmentcategory | Yes |
| settings | Personal settings | Active staff | Yes |
| stations | Stations | stations.view_station | No |
| offline-preparation | Offline event preparation | events.view_offlineeventpreparation | No |

Create Customer is excluded because no implemented destination exists. Fewer than nine selected actions leave unused positions empty. Existing Phase 1B appearance and dashboard grid remain intact.

Migration accounts.0002_user_quick_action_ids only adds a nullable JSON field to existing users. No data deletion, data import or production configuration change. Render preview applies it through its existing migrate --noinput pre-deploy step.

Scope excludes live notifications, Analytics customization, new destination workflows and other portal dashboards.
