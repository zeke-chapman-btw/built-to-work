from django.urls import path
from . import views
app_name="reporting"
urlpatterns=[
 path("",views.workspace,name="workspace"),
 path("events/<uuid:event_id>/",views.event_report,name="event"),
 path("events/<uuid:event_id>/people/",views.event_participants,name="event-people"),
 path("events/<uuid:event_id>/figures/new/",views.manual_figure,name="figure-new"),
 path("events/<uuid:event_id>/figures/<uuid:figure_id>/",views.manual_figure,name="figure-edit"),
 path("payroll/start/",views.payroll_start,name="payroll-start"),
 path("payroll/<str:starts_on>/",views.payroll_detail,name="payroll"),
 path("adjustments/new/",views.adjustment,name="adjustment-new"),
 path("adjustments/<uuid:adjustment_id>/void/",views.adjustment_void,name="adjustment-void"),
 path("my-pay/",views.my_pay,name="my-pay"),
 path("my-pay/<uuid:version_id>/",views.my_statement,name="my-statement"),
]
