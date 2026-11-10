from django.urls import path
from . import staff_views,login_views,staff_member_views
app_name='accounts'
urlpatterns=[
 path('login/',login_views.sign_in,name='login'),
 path('login/choose/',login_views.identity_choice,name='identity-choice'),
 path('logout/',login_views.sign_out,name='logout'),
 path('password-change/',login_views.password_change,name='password-change'),
 path('password-reset/',login_views.recovery,name='recovery'),
 path('staff/',staff_views.index,name='staff-index'),
 path('staff/create/',staff_member_views.add,name='staff-create'),
 path('staff/profile/<uuid:profile_id>/my-pay/',staff_member_views.own_pay_access,name='staff-own-pay'),
 path('staff/<uuid:user_id>/',staff_views.detail,name='staff-detail'),
 path('staff/<uuid:user_id>/edit/',staff_views.edit,name='staff-edit'),
 path('staff/<uuid:user_id>/<str:action>/',staff_views.action,name='staff-action'),
 path('activate/<str:token>/',staff_views.activate,name='activate'),
]
