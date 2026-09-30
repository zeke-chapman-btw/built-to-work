from django.urls import path
from . import views
app_name = 'internal'
urlpatterns = [path('', views.home, name='home')]
