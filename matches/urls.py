from django.urls import path

from . import views

urlpatterns = [
    # Public
    path("", views.HomeView.as_view(), name="home"),
    path("api/pitches/", views.pitches_api, name="pitches_api"),
    path("matches/<int:pk>/", views.MatchDetailView.as_view(), name="match_detail"),

    # Organizer
    path("organizer/matches/", views.MyMatchesView.as_view(), name="my_matches"),
    path("organizer/matches/new/", views.MatchCreateView.as_view(), name="match_create"),
    path("organizer/matches/<int:pk>/edit/", views.MatchUpdateView.as_view(), name="match_update"),
    path("organizer/matches/<int:pk>/cancel/", views.MatchCancelView.as_view(), name="match_cancel"),
    path("organizer/pitches/new/", views.PitchCreateView.as_view(), name="pitch_create"),
    path(
        "organizer/participations/<int:pk>/remove/",
        views.RemovePlayerView.as_view(),
        name="participation_remove",
    ),
]