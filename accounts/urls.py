from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("login/", views.CustomLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("organizer/", views.OrganizerDashboardView.as_view(), name="organizer_dashboard"),
]