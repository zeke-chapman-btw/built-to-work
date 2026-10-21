"""Canonical, permission-aware staff workspace navigation."""
from django.urls import reverse

WORKSPACES = (
    {"id":"home","label":"HOME","icon":"home","url_name":"internal:home","description":"Your authorized BTW work at a glance.","permissions":()},
    {"id":"events","label":"EVENTS","icon":"calendar","url_name":"events:event_list","description":"Events, registrations, and ticket workflows.","permissions":("events.view_event","events.add_event","events.view_eventregistration","staffing.manage_event_staffing")},
    {"id":"participants","label":"PARTICIPANTS","icon":"people","url_name":"internal:workspace","url_kwargs":{"workspace_id":"participants"},"description":"Existing participant records and review workflows.","permissions":("participants.view_participant","participants.view_participantqualification","participants.view_participantdocument","participants.view_qualificationapprovals","participants.view_historicalimportbatch","participants.view_participantaccountrequest")},
    {"id":"tasks","label":"TASKS","icon":"tasks","url_name":"operations:tasks","description":"Your tasks, assignments and shared activity.","permissions":()},
    {"id":"operations","label":"OPERATIONS","icon":"operations","url_name":"internal:workspace","url_kwargs":{"workspace_id":"operations"},"description":"Stations, trailer operations, synchronization, and assessments.","permissions":("events.view_event","stations.view_station","events.view_trailer","events.view_syncoperation","events.view_ticketdelivery","events.view_matchreview","events.view_operationalincident","assessments.view_assessmentcategory","assessments.add_assessmentcategory","staffing.view_staffprofile","staffing.manage_event_staffing","operations.manage_tasks","operations.manage_event_notes","operations.manage_event_documents","workdays.manage_workdays","event_reports.view_event_reports")},
    {"id":"customers","label":"CUSTOMERS","icon":"customers","url_name":"internal:workspace","url_kwargs":{"workspace_id":"customers"},"description":"Customer management is planned; no customer records or tools are available yet.","permissions":(),"planned":True},
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
 'participants': ({'label': 'Directory',
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
 'operations': ({'label': 'Operational monitoring',
                 'description': 'Review synchronization, deliveries, and operational incidents.',
                 'url_name': 'events:operations-dashboard',
                 'permissions': ('events.view_syncoutboxitem',)},
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
                {'label': 'Registration Forms',
                 'description': 'Manage registration questions and published form versions.',
                 'url_name': 'participants:registration-forms',
                 'permissions': ('participants.change_registrationform',)}),
 'administration': ({'label': 'Staff directory',
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
                    {'label': 'Registration form builder',
                     'description': 'Configure published registration forms.',
                     'url_name': 'participants:registration-forms',
                     'permissions': ('participants.change_registrationform',)},
                    {'label': 'Test Participant',
                     'description': 'Manage the reserved test participant.',
                     'url_name': 'participants:testing',
                     'permissions': ('participants.view_testparticipant',
                                     'participants.change_testparticipant')})}

ACTIVE_PREFIXES = {
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

def visible_workspaces(user,current_path=""):
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
        visible.append(entry)
    return visible

def visible_settings_link(user,current_path=""):
    if not getattr(user,"is_active",False) or not getattr(user,"is_staff",False):
        return None
    item=_resolved({"label":"SETTINGS","icon":"settings","url_name":"internal:settings"})
    item["active"]=current_path==item["url"] or current_path.startswith("/workdays/mine/")
    return item

def visible_workspace_links(user,workspace_id):
    from .reporting import report_destinations
    return [_resolved(item) for item in WORKSPACE_LINKS.get(workspace_id,()) if _can_see(user,item["permissions"]) and _destination_allowed(user,item) and (item["url_name"]!="internal:reports" or report_destinations(user))]


def _destination_allowed(user, item):
    """Match complete existing endpoint policies, without granting permissions."""
    route=item["url_name"]
    if route in ("workdays:index","workdays:reports"):
        from apps.workdays.services import manager
        return manager(user)
    if route=="staffing:queue":
        return user.has_perms(("staffing.manage_event_staffing","events.view_event"))
    if route=="participants:registration-forms":
        return user.is_superuser
    return True
