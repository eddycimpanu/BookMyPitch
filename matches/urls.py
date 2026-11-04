from django.urls import path

from . import views

urlpatterns = [
    # Public
    path("", views.HomeView.as_view(), name="home"),
    path("api/pitches/", views.pitches_api, name="pitches_api"),
    path("matches/<int:pk>/", views.MatchDetailView.as_view(), name="match_detail"),
    path("pitches/<int:pk>/", views.PitchDetailView.as_view(), name="pitch_detail"),

    # Players (any logged-in user)
    path("matches/<int:pk>/join/", views.JoinMatchView.as_view(), name="match_join"),
    path("matches/<int:pk>/leave/", views.LeaveMatchView.as_view(), name="match_leave"),
    path("matches/<int:pk>/waitlist/join/", views.JoinWaitlistView.as_view(), name="waitlist_join"),
    path("matches/<int:pk>/waitlist/leave/", views.LeaveWaitlistView.as_view(), name="waitlist_leave"),
    path("matches/<int:pk>/rate/<int:user_pk>/", views.RatePlayerView.as_view(), name="rate_player"),
    path("matches/<int:pk>/chat/", views.MatchChatView.as_view(), name="match_chat"),
    path("joined/", views.JoinedMatchesView.as_view(), name="joined_matches"),

    # Organizer
    path("organizer/matches/", views.MyMatchesView.as_view(), name="my_matches"),
    path("organizer/matches/new/", views.MatchCreateView.as_view(), name="match_create"),
    path("organizer/matches/<int:pk>/edit/", views.MatchUpdateView.as_view(), name="match_update"),
    path("organizer/matches/<int:pk>/cancel/", views.MatchCancelView.as_view(), name="match_cancel"),
    path("organizer/pitches/new/", views.PitchCreateView.as_view(), name="pitch_create"),
    path("organizer/pitches/<int:pk>/book/", views.BookPitchView.as_view(), name="pitch_book"),
    path(
        "organizer/participations/<int:pk>/remove/",
        views.RemovePlayerView.as_view(),
        name="participation_remove",
    ),
    path(
        "organizer/participations/<int:pk>/no-show/",
        views.NoShowView.as_view(),
        name="participation_no_show",
    ),

    # Pitch owners
    path("owner/pitches/", views.OwnerPitchListView.as_view(), name="owner_pitches"),
    path("owner/pitches/new/", views.OwnerPitchFormView.as_view(), name="owner_pitch_create"),
    path("owner/pitches/<int:pk>/edit/", views.OwnerPitchFormView.as_view(), name="owner_pitch_update"),
    path("owner/bookings/", views.OwnerBookingsView.as_view(), name="owner_bookings"),
    path("owner/bookings/<int:pk>/cancel/", views.OwnerCancelBookingView.as_view(), name="owner_booking_cancel"),
    path("owner/blocked/<int:pk>/remove/", views.OwnerUnblockTimeView.as_view(), name="owner_unblock"),
]
