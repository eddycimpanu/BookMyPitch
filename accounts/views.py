from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, UpdateView

from matches.reputation import matches_played, reputation

from .forms import LoginForm, ProfileForm, SignUpForm
from .models import User


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        messages.success(self.request, f"Welcome to BookMyPitch, {self.object.username}!")
        return response


class CustomLoginView(LoginView):
    authentication_form = LoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class ProfileView(DetailView):
    model = User
    template_name = "accounts/profile.html"
    context_object_name = "profile_user"
    slug_field = "username"
    slug_url_kwarg = "username"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = self.object
        if person.is_owner:
            context["pitches"] = person.owned_pitches.order_by("name")
        else:
            context["stats"] = reputation(person)
            context["recent_matches"] = matches_played(person).select_related("pitch").order_by("-start_time")[:5]
        return context


class ProfileEditView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "accounts/profile_form.html"

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profile saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("profile", args=[self.request.user.username])
