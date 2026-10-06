from django.urls import path
from . import views
app_name = 'games'
urlpatterns = [
    path('station/<str:station_code>/<uuid:session_id>/launch/', views.launch, name='launch'),
    path('sessions/<uuid:game_session_id>/test-complete/', views.complete_test, name='test_complete'),
    path('station/<str:station_code>/<uuid:session_id>/simulator-next/', views.simulator_next, name='simulator_next'),
]
