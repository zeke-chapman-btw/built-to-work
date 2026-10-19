from django.urls import path
from . import views
app_name="operations"
urlpatterns=[
 path("my-tasks/",views.task_list,{"mine":True},name="my-tasks"),
 path("tasks/",views.task_list,name="tasks"),
 path("tasks/create/",views.task_edit,name="task-add"),
 path("tasks/<uuid:task_id>/",views.task_detail,name="task"),
 path("tasks/<uuid:task_id>/edit/",views.task_edit,name="task-edit"),
 path("tasks/<uuid:task_id>/status/",views.task_status,name="task-status"),
 path("events/",views.events_index,name="events"),
 path("events/<uuid:event_id>/tasks/create/",views.task_edit,name="event-task-add"),
 path("events/<uuid:event_id>/documents/upload/",views.document_upload,name="upload"),
 path("events/<uuid:event_id>/documents/demo/",views.document_upload,{"demo":True},name="demo"),
 path("events/<uuid:event_id>/<str:section>/",views.event_operations,name="event"),
 path("notes/<uuid:note_id>/edit/",views.note_edit,name="note-edit"),
 path("records/<str:kind>/<uuid:obj_id>/archive/",views.archive,name="archive"),
 path("documents/<uuid:document_id>/download/",views.download,name="download"),
]
