from django.urls import path
from . import views
app_name='event_reports'
urlpatterns=[
 path('',views.index,name='index'),
 path('categories/',views.categories,name='categories'),
 path('categories/add/',views.category_edit,name='category-add'),
 path('categories/<uuid:category_id>/',views.category_edit,name='category-edit'),
 path('events/<uuid:event_id>/',views.detail,name='detail'),
 path('events/<uuid:event_id>/expenses/add/',views.entry,name='expense-add'),
 path('events/<uuid:event_id>/incentives/add/',views.entry,{'kind':'incentive'},name='incentive-add'),
 path('events/<uuid:event_id>/review/',views.review,name='review'),
 path('expenses/<uuid:record_id>/edit/',views.entry,name='expense-edit'),
 path('incentives/<uuid:record_id>/edit/',views.entry,{'kind':'incentive'},name='incentive-edit'),
 path('records/<str:kind>/<uuid:record_id>/<str:action>/',views.action,name='action'),
]
