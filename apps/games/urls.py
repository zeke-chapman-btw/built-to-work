from django.urls import path
from . import views, duck_views
app_name = 'games'
from . import manage_views
urlpatterns = [
    path('manage/<uuid:game_id>/edit/', manage_views.edit, name='manage_edit'),
    path('station/<str:station_code>/<uuid:session_id>/duck/<uuid:game_session_id>/start/', duck_views.start, name='duck_start'),
    path('station/<str:station_code>/<uuid:session_id>/duck/<uuid:game_session_id>/finish/', duck_views.finish, name='duck_finish'),
    path('station/<str:station_code>/<uuid:session_id>/duck/<uuid:game_session_id>/replay/', duck_views.replay, name='duck_replay'),
    path('station/<str:station_code>/<uuid:session_id>/duck/<uuid:game_session_id>/restart/', duck_views.restart, name='duck_restart'),
    path('station/<str:station_code>/<uuid:session_id>/launch/', views.launch, name='launch'),
    path('sessions/<uuid:game_session_id>/test-complete/', views.complete_test, name='test_complete'),
    path('station/<str:station_code>/<uuid:session_id>/simulator-next/', views.simulator_next, name='simulator_next'),
]
