from django.urls import path

from . import views

app_name = "reminders"
urlpatterns = [
    path("", views.reminder_list, name="list"),
    path("mine/", views.mine, name="mine"),
    path("switch/", views.switch, name="switch"),
    path("new/", views.rule_edit, name="new"),
    path("<int:pk>/", views.rule_edit, name="edit"),
    path("roles/new/", views.role_edit, name="role-new"),
    path("roles/<int:pk>/", views.role_edit, name="role-edit"),
    path("notices/read/", views.notice_read, name="read-all"),
    path("notices/<int:pk>/read/", views.notice_read, name="read"),
]
