from django.urls import path

from . import views

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("api/pitches/", views.pitches_api, name="pitches_api"),
    path("matches/<int:pk>/", views.MatchDetailView.as_view(), name="match_detail"),
]