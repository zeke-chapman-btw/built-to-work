from django.urls import path
from . import views

app_name = 'recruiting'
urlpatterns = [
    path('portal/profile/', views.portal_preferences, name='portal-preferences'),
    path('lists/', views.lists, name='lists'),
    path('lists/<uuid:list_id>/', views.candidate_list, name='list'),
    path('people/<int:participant_id>/', views.candidate_foundation, name='candidate'),
    path('identity/<int:source_id>/<int:target_id>/', views.consolidation, name='consolidation'),
    path('settings/classifications/', views.classifications, name='classifications'),
    path('settings/classifications/<uuid:classification_id>/', views.classifications, name='classification-edit'),
    path('campaigns/<uuid:campaign_id>/policy/', views.campaign_policy, name='campaign-policy'),
]
