from django.urls import path
from . import views, matching_views

app_name = 'recruiting'
urlpatterns = [
    path('matching/',matching_views.matching_directory,name='matching-directory'),
    path('matching/<uuid:opportunity_id>/',matching_views.matching_results,name='matching'),
    path('matching/<uuid:opportunity_id>/criteria/',matching_views.criteria_editor,name='criteria'),
    path('matching/<uuid:opportunity_id>/select/<int:participant_id>/',matching_views.select_candidate,name='select-candidate'),
    path('portal/profile/', views.portal_preferences, name='portal-preferences'),
    path('lists/', views.lists, name='lists'),
    path('lists/<uuid:list_id>/', views.candidate_list, name='list'),
    path('people/<int:participant_id>/', views.candidate_foundation, name='candidate'),
    path('identity/<int:source_id>/<int:target_id>/', views.consolidation, name='consolidation'),
    path('settings/classifications/', views.classifications, name='classifications'),
    path('settings/classifications/<uuid:classification_id>/', views.classifications, name='classification-edit'),
    path('campaigns/<uuid:campaign_id>/policy/', views.campaign_policy, name='campaign-policy'),
]
