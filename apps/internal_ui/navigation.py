"""Canonical, permission-aware staff workspace navigation."""
from django.urls import reverse

WORKSPACES = (
    {"id":"home","label":"HOME","icon":"home","url_name":"internal:home","description":"Your authorized BTW work at a glance.","permissions":()},
    {"id":"events","label":"EVENTS","icon":"calendar","url_name":"events:event_list","description":"Events, registrations, and ticket workflows.","permissions":("events.view_event","events.add_event","events.view_eventregistration","staffing.manage_event_staffing")},
    {"id":"participants","label":"PARTICIPANTS","icon":"people","url_name":"internal:workspace","url_kwargs":{"workspace_id":"participants"},"description":"Existing participant records and review workflows.","permissions":("participants.view_participant","participants.view_participantqualification","participants.view_participantdocument","participants.view_qualificationapprovals","participants.view_historicalimportbatch","participants.view_participantaccountrequest")},
    {"id":"tasks","label":"TASKS","icon":"tasks","url_name":"operations:tasks","description":"Your tasks, assignments and shared activity.","permissions":()},
    {"id":"operations","label":"OPERATIONS","icon":"operations","url_name":"internal:workspace","url_kwargs":{"workspace_id":"operations"},"description":"Stations, trailer operations, synchronization, and assessments.","permissions":("events.view_event","stations.view_station","events.view_trailer","events.view_syncoperation","events.view_ticketdelivery","events.view_matchreview","events.view_operationalincident","assessments.view_assessmentcategory","assessments.add_assessmentcategory","staffing.view_staffprofile","staffing.manage_event_staffing","operations.view_operationstemplate","operations.manage_tasks","operations.manage_event_notes","operations.manage_event_documents","workdays.manage_workdays","event_reports.view_event_reports","operations.manage_event_execution","operations.manage_master_checklist","participants.view_consentacceptance")},
    {"id":"platform-tools","label":"PLATFORM TOOLS","icon":"operations","url_name":"internal:workspace","url_kwargs":{"workspace_id":"platform-tools"},"description":"Existing platform monitoring, reconciliation and recovery tools.","permissions":("events.view_syncoutboxitem","events.view_participantmatchreview","events.view_backuprecord")},
    {"id":"customers","label":"CUSTOMERS","icon":"customers","url_name":"internal:workspace","url_kwargs":{"workspace_id":"customers"},"description":"Customer management is planned; no customer records or tools are available yet.","permissions":(),"planned":True},
    {"id":"campaigns","label":"CAMPAIGNS","icon":"campaigns","url_name":"internal:workspace","url_kwargs":{"workspace_id":"campaigns"},"description":"Campaign management is planned; no campaign records or tools are available yet.","permissions":(),"planned":True},
    {"id":"approvals","label":"APPROVALS","icon":"approvals","url_name":"internal:workspace","url_kwargs":{"workspace_id":"approvals"},"description":"A unified approvals workspace is planned; existing review queues remain in their current workspaces.","permissions":(),"planned":True},
    {"id":"administration","label":"ADMIN","icon":"settings","url_name":"internal:workspace","url_kwargs":{"workspace_id":"administration"},"description":"Authorized BTW configuration and staff administration.","permissions":("staffing.view_staffprofile","workdays.manage_workdays","auth.view_user","auth.change_user","participants.change_registrationform","participants.view_testparticipant","participants.change_testparticipant","workdays.manage_compensation","event_reports.manage_expense_categories")},
)

WORKSPACE_LINKS = {'events': ({'label': 'My staff schedule',
             'description': 'Published opportunities, own requests and confirmed assignments.',
             'url_name': 'staffing:schedule',
             'permissions': ()},
            {'label': 'Events',
             'description': 'View event schedules, configuration, and existing operations.',
             'url_name': 'events:event_list',
             'permissions': ('events.view_event', 'events.add_event', 'events.view_eventregistration')},
            {'label': 'Staff availability requests',
             'description': 'Review pending requests and open event staffing.',
             'url_name': 'staffing:queue',
             'permissions': ('staffing.manage_event_staffing',)},
            {'label': 'Event notes and documents',
             'description': 'Authorized event notes and operational attachments.',
             'url_name': 'operations:events',
             'permissions': ()}),
 'participants': ({'label':'Participant Review Queue','description':'Review intake identity conflicts and eligible profile updates.','url_name':'participants:intake-review-queue','permissions':('events.view_participantmatchreview',)},
                  {'label': 'Directory',
                   'description': 'Search participants and open their unified profile.',
                   'url_name': 'participants:staff-directory',
                   'permissions': ('participants.view_participant',)},
                  {'label': 'Qualifications',
                   'description': 'Review and maintain recorded qualifications.',
                   'url_name': 'participants:staff-qualifications',
                   'permissions': ('participants.view_participantqualification',)},
                  {'label': 'Documents',
                   'description': 'Access the authorized document-management queue.',
                   'url_name': 'participants:staff-document-directory',
                   'permissions': ('participants.view_participantdocument',)},
                  {'label': 'Verification approvals',
                   'description': 'Review pending qualification verification decisions.',
                   'url_name': 'participants:staff-qualification-approvals',
                   'permissions': ('participants.view_qualificationapprovals',)},
                  {'label': 'Historical records',
                   'description': 'Import and export authorized historical participant records.',
                   'url_name': 'participants:historical-index',
                   'permissions': ('participants.view_historicalimportbatch',
                                   'participants.add_historicalimportbatch',
                                   'participants.export_historicalparticipant')},
                  {'label': 'Account requests',
                   'description': 'Review participant account requests.',
                   'url_name': 'participants:staff-requests',
                   'permissions': ('participants.view_participantaccountrequest',
                                   'participants.change_participantaccountrequest')}),
 'operations': ({'label':'Consents','description':'Upload, review and publish consent documents; view versions and acceptance history.','url_name':'participants:consent-index','permissions':()},
                {'label':'Form Builder','description':'Create and manage participant intake forms.','url_name':'participants:intake-config-index','permissions':()},
                {'label':'Event Execution','description':'Event responsibilities, simple checklist progress and closeout.','url_name':'operations:execution-index','permissions':()},
                {'label':'Master event checklist','description':'Administrator-controlled defaults for future event checklists.','url_name':'operations:master-checklist','permissions':('operations.manage_master_checklist',)},
                {'label':'Trailers','description':'Existing trailer display settings; credentials remain in restricted administration.','url_name':'operations:trailers','permissions':('events.view_trailerinstance',)},
                {'label': 'Operations Templates',
                 'description': 'Reusable published revisions and controlled event setup.',
                 'url_name': 'operations:templates',
                 'permissions': ('operations.view_operationstemplate',)},
                {'label': 'Offline event preparation',
                 'description': 'Prepare events for authorized trailer use.',
                 'url_name': 'events:offline-preparations',
                 'permissions': ('events.view_offlineeventpreparation',
                                 'events.add_offlineeventpreparation')},
                {'label': 'Station Management',
                 'description': 'Open the existing station operations workspace.',
                 'url_name': 'stations:list',
                 'permissions': ('stations.view_station',)},
                {'label': 'Quizzes & Tests',
                 'description': 'Manage assessment content using existing workflows.',
                 'url_name': 'assessments:dashboard',
                 'permissions': ('assessments.view_assessmentcategory',
                                 'assessments.add_assessmentcategory')},
                {'label': 'Games / Duck Hunt',
                 'description': 'Manage available games and event game settings.',
                 'url_name': 'admin:games_gamedefinition_changelist',
                 'permissions': ('games.view_gamedefinition', 'games.change_gamedefinition')},
                ),
 'platform-tools': ({'label':'Platform monitoring','description':'Existing synchronization, delivery and incident monitoring. Permanent administration home to be reviewed.','url_name':'events:operations-dashboard','permissions':('events.view_syncoutboxitem',)},
                    {'label':'Participant match review','description':'Existing reversible offline identity review. Intended future home: participant reconciliation.','url_name':'events:match-review-list','permissions':('events.view_participantmatchreview',)},
                    {'label':'Backup records','description':'Existing read-only backup records in restricted administration.','url_name':'admin:events_backuprecord_changelist','permissions':('events.view_backuprecord',)}),
 'administration': ({'label':'Portal Preview','description':'Review portal experiences using your administrator account and isolated synthetic data.','url_name':'internal:portal-preview','permissions':()},
                   {'label': 'Staff directory',
                     'description': 'Manage staff profiles and assignment history.',
                     'url_name': 'staffing:directory',
                     'permissions': ('staffing.view_staffprofile',)},
                    {'label': 'Staff Workdays & Mileage',
                     'description': 'Manage event-day rosters, corrections and individual trips.',
                     'url_name': 'workdays:index',
                     'permissions': ('workdays.manage_workdays',)},
                    {'label': 'Expense Categories',
                     'description': 'Configure expense categories and incentive-purchase treatment.',
                     'url_name': 'event_reports:categories',
                     'permissions': ('event_reports.manage_expense_categories',)},
                    {'label': 'Staff Compensation Settings',
                     'description': 'Manage staff roles, daily pay, per diem and mileage rates.',
                     'url_name': 'workdays:settings',
                     'permissions': ('workdays.manage_compensation',)},
                    {'label': 'Django administration',
                     'description': 'Open the restricted Django configuration interface.',
                     'url_name': 'admin:index',
                     'permissions': ('auth.view_user', 'auth.change_user')},

                    {'label': 'Test Participant',
                     'description': 'Manage the reserved test participant.',
                     'url_name': 'participants:testing',
                     'permissions': ('participants.view_testparticipant',
                                     'participants.change_testparticipant')})}

ACTIVE_PREFIXES = {
    "platform-tools":("/events/operations/",),
    "tasks":("/operations/tasks/","/operations/my-tasks/"),
    "events":("/events/","/staff/"),
    "participants":("/participants/",),
    "operations":("/internal/reports/","/reports/","/workdays/events/","/workdays/records/","/workdays/trips/","/workdays/mine/","/workdays/reports/","/operations/","/staff/directory/","/staff/requests/","/events/operations/","/events/offline-preparations/","/stations/","/assessments/","/quizzes/"),
    "administration":("/reports/categories/","/workdays/compensation/","/admin/","/participants/staff/registration-forms/","/participants/staff/testing/"),
}

def _can_see(user, permissions):
    return not permissions or user.is_superuser or any(user.has_perm(permission) for permission in permissions)

def _resolved(item):
    result=dict(item)
    result["url"]=reverse(item["url_name"],kwargs=item.get("url_kwargs",{}))
    return result

def visible_legacy_workspaces(user,current_path=""):
    if not getattr(user,"is_active",False) or not getattr(user,"is_staff",False):
        return []
    visible=[]
    for item in WORKSPACES:
        if not _can_see(user,item["permissions"]):
            from apps.staffing.models import StaffProfile
            if item["id"] not in ("events","operations") or not StaffProfile.objects.filter(user=user,is_active=True).exists():
                continue
        entry=_resolved(item)
        if item["id"]=="events" and not user.has_perm("events.view_event"):
            entry["url"]=reverse("events:event_create" if user.has_perm("events.add_event") else "staffing:schedule")
        entry["active"]=current_path==entry["url"] or any(current_path.startswith(prefix) for prefix in ACTIVE_PREFIXES.get(item["id"],()))
        if item["id"]=="operations" and current_path.startswith(("/operations/tasks/","/operations/my-tasks/")):
            entry["active"]=False
        if item["id"]=="operations" and current_path.startswith("/reports/categories/"):
            entry["active"]=False
        if item["id"]=="operations" and current_path=="/workdays/":
            entry["active"]=False
        if item["id"]=="events" and current_path.startswith(("/staff/directory/","/staff/requests/","/events/operations/","/events/offline-preparations/")):
            entry["active"]=False
        if item["id"]=="participants" and current_path.startswith(("/participants/staff/registration-forms/","/participants/staff/testing/")):
            entry["active"]=False
        if item["id"]=="operations" and current_path.startswith(("/operations/events/","/operations/notes/","/operations/documents/","/operations/records/","/staff/requests/","/staff/directory/","/workdays/events/","/workdays/records/","/workdays/trips/")):
            entry["active"]=False
        if item["id"]=="events" and current_path.startswith(("/operations/events/","/operations/notes/","/operations/documents/","/operations/records/","/staff/requests/")):
            entry["active"]=True
        if item["id"]=="events" and current_path.startswith("/staff/directory/"):
            entry["active"]=False
        if item["id"]=="administration" and (current_path.startswith("/staff/directory/") or current_path=="/workdays/"):
            entry["active"]=True
        if current_path.startswith("/workdays/mine/"):
            entry["active"]=False
        if current_path.startswith(("/reports/events/","/workdays/events/")) and item["id"] in ("events","operations"):
            entry["active"]=item["id"]=="events"
        if current_path.startswith("/events/operations/") and item["id"] in ("operations","platform-tools"):
            entry["active"]=item["id"]=="platform-tools"
        if current_path.startswith("/operations/execution/events/") and item["id"] in ("events","operations"):
            entry["active"]=item["id"]=="events"
        if current_path.startswith(("/participants/staff/intake-forms/","/participants/staff/intake-trailer/","/participants/staff/consents/")):
            entry["active"]=item["id"]=="operations"
        visible.append(entry)
        if current_path.startswith("/operations/setup/events/") and item["id"] in ("events","operations"):
            entry["active"]=item["id"]=="events"
    return visible

def visible_settings_link(user,current_path=""):
    if not getattr(user,"is_active",False) or not getattr(user,"is_staff",False):
        return None
    item=_resolved({"label":"SETTINGS","icon":"settings","url_name":"internal:settings"})
    item["active"]=current_path.startswith("/internal/settings/") or current_path.startswith("/workdays/mine/")
    return item

def visible_workspace_links(user,workspace_id):
    from .reporting import report_destinations
    return [_resolved(item) for item in WORKSPACE_LINKS.get(workspace_id,()) if _can_see(user,item["permissions"]) and _destination_allowed(user,item) and (item["url_name"]!="internal:reports" or report_destinations(user))]


def _destination_allowed(user, item):
    """Match complete existing endpoint policies, without granting permissions."""
    route=item["url_name"]
    if route=="internal:portal-preview":return user.is_superuser
    if route=="operations:trailers":return user.is_superuser
    if route in ("workdays:index","workdays:reports"):
        from apps.workdays.services import manager
        return manager(user)
    if route=="staffing:queue":
        return user.has_perms(("staffing.manage_event_staffing","events.view_event"))
    if route=="participants:consent-index":return user.is_superuser or user.has_perm("participants.view_consentacceptance")
    if route=="participants:intake-config-index":return user.is_superuser
    if route=="participants:intake-review-queue":return user.has_perms(("participants.view_participant","events.view_participantmatchreview"))
    if route=="participants:registration-forms":
        return user.is_superuser
    return True


def staff_destinations(user, current_path=""):
    """Owner-approved staff navigation; existing endpoint policies still guard tools."""
    legacy = {entry["id"]: entry for entry in visible_legacy_workspaces(user, current_path)}
    if not legacy:
        return []
    labels = {"home": "Home", "events": "Events", "participants": "Participants",
              "operations": "Operations", "tasks": "Tasks", "customers": "Customers",
              "campaigns": "Campaigns", "approvals": "Approvals"}
    entries = []
    for key in ("home", "tasks", "participants", "customers", "campaigns", "approvals", "events", "operations"):
        if key in legacy:
            entries.append(dict(legacy[key], label=labels[key]))
    entries.extend([
        dict(id="reports", label="Reports & Analytics", icon="reports", url=reverse("internal:reports"), active=False),
        dict(id="settings", label="Settings & Administration", icon="settings", url=reverse("internal:settings"), active=False),
    ])
    active = next((entry["id"] for entry in entries if entry["active"]), None)
    if any(legacy.get(key, {}).get("active") for key in ("administration", "platform-tools")):
        active = "settings"
    if current_path.startswith(("/internal/reports/", "/reports/", "/workdays/reports/")):
        active = "events" if current_path.startswith("/reports/events/") else "settings" if current_path.startswith("/reports/categories/") else "reports"
    if current_path.startswith(("/internal/settings/", "/workdays/mine/", "/internal/quick-actions/")):
        active = "settings"
    for entry in entries:
        entry["active"] = entry["id"] == active
    return entries


def settings_groups(user):
    """Rehome existing destinations without widening their access policies."""
    allowed = {entry["id"] for entry in visible_legacy_workspaces(user)}
    groups = []
    for key, label in (("administration", "Administration"), ("platform-tools", "Platform tools")):
        if key in allowed:
            links = visible_workspace_links(user, key)
            if links:
                groups.append(dict(label=label, links=links))
    # Retain the existing planned destination, without adding customer features.
    if "customers" in allowed:
        groups.append(dict(label="Other destinations", links=[dict(label="Customers", description="Planned workspace", url=reverse("internal:workspace", kwargs={"workspace_id": "customers"}))]))
    return groups


# Existing integrations and their context keep the established registry contract.
# Only the shared shell opts in to the owner-approved primary destinations.
visible_workspaces = visible_legacy_workspaces


def legacy_workspace_context(user, current_path=""):
    return next((entry for entry in visible_legacy_workspaces(user, current_path)
                 if entry["active"] and entry["id"] in ("administration", "platform-tools", "customers")), None)
