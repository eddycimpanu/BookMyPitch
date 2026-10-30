from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from accounts.mixins import OrganizerRequiredMixin

from .forms import MatchForm, PitchForm
from .models import Match, Participation, Pitch
from .services import LEAVE_DEADLINE, MatchActionError, join_match, leave_match


def day_label(value):
    """Return 'Today', 'Tomorrow' or a date like 'Saturday 1 November', in local time."""
    day = timezone.localdate(value)
    today = timezone.localdate()
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return f"{day:%A} {day.day} {day:%B}"


class HomeView(TemplateView):
    template_name = "home.html"


def pitches_api(request):
    """Return pitches that have upcoming, non-cancelled matches, as JSON for the map."""
    now = timezone.now()
    visible_statuses = [Match.Status.ACTIVE, Match.Status.FULL]

    upcoming_matches = (
        Match.objects.filter(start_time__gt=now, status__in=visible_statuses)
        .annotate(taken=Count("participations"))
        .order_by("start_time")
    )

    pitches = (
        Pitch.objects.filter(
            matches__start_time__gt=now,
            matches__status__in=visible_statuses,
        )
        .distinct()
        .prefetch_related(Prefetch("matches", queryset=upcoming_matches, to_attr="upcoming_matches"))
    )

    data = []
    for pitch in pitches:
        data.append({
            "id": pitch.id,
            "name": pitch.name,
            "address": pitch.address,
            "sector": pitch.get_sector_display(),
            "lat": float(pitch.latitude),
            "lng": float(pitch.longitude),
            "matches": [
                {
                    "id": match.id,
                    "start_iso": match.start_time.isoformat(),
                    "day": day_label(match.start_time),
                    "time": timezone.localtime(match.start_time).strftime("%H:%M"),
                    "spots_needed": match.spots_needed,
                    "spots_taken": min(match.taken, match.spots_needed),
                    "spots_left": max(match.spots_needed - match.taken, 0),
                    "price": str(match.price_per_player),
                    "url": reverse("match_detail", args=[match.pk]),
                }
                for match in pitch.upcoming_matches
            ],
        })

    return JsonResponse({"pitches": data})

class MatchDetailView(DetailView):
    model = Match
    template_name = "matches/match_detail.html"
    context_object_name = "match"

    def get_queryset(self):
        return Match.objects.select_related("pitch", "organizer").prefetch_related("participations__player")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        match = self.object
        user = self.request.user

        is_joined = user.is_authenticated and any(
            participation.player_id == user.id for participation in match.participations.all()
        )
        is_open = match.status != Match.Status.CANCELLED and not match.has_started

        context["open_slots"] = range(match.spots_left)
        context["is_joined"] = is_joined
        context["can_join"] = is_open and not is_joined and not match.is_full and user != match.organizer
        context["can_leave"] = is_joined and timezone.now() <= match.start_time - LEAVE_DEADLINE
        return context
    
# ---------- Organizer views ----------


class MyMatchesView(OrganizerRequiredMixin, ListView):
    template_name = "matches/my_matches.html"
    context_object_name = "matches"

    def get_queryset(self):
        return (
            Match.objects.filter(organizer=self.request.user)
            .select_related("pitch")
            .prefetch_related("participations__player")
            .order_by("start_time")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        upcoming, past = [], []
        for match in context["matches"]:
            is_upcoming = match.start_time > now and match.status != Match.Status.CANCELLED
            (upcoming if is_upcoming else past).append(match)
        context["upcoming"] = upcoming
        context["past"] = list(reversed(past))
        return context


class PitchCreateView(OrganizerRequiredMixin, CreateView):
    model = Pitch
    form_class = PitchForm
    template_name = "matches/pitch_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f"{form.instance.name} was added. Now set up your match.")
        return super().form_valid(form)

    def get_success_url(self):
        # Go straight to "Create match" with the new pitch already selected
        return f"{reverse('match_create')}?pitch={self.object.pk}"


class MatchCreateView(OrganizerRequiredMixin, CreateView):
    model = Match
    form_class = MatchForm
    template_name = "matches/match_form.html"
    success_url = reverse_lazy("my_matches")

    def get_initial(self):
        initial = super().get_initial()
        pitch_id = self.request.GET.get("pitch", "")
        if pitch_id.isdigit():
            initial["pitch"] = pitch_id
        return initial

    def form_valid(self, form):
        form.instance.organizer = self.request.user
        messages.success(self.request, "Match created. It is now on the map.")
        return super().form_valid(form)


class MatchUpdateView(OrganizerRequiredMixin, UpdateView):
    model = Match
    form_class = MatchForm
    template_name = "matches/match_form.html"
    success_url = reverse_lazy("my_matches")

    def get_queryset(self):
        # Only your own upcoming, non-cancelled matches. Anything else returns 404.
        return Match.objects.filter(
            organizer=self.request.user,
            start_time__gt=timezone.now(),
        ).exclude(status=Match.Status.CANCELLED)

    def form_valid(self, form):
        form.instance.refresh_status()
        messages.success(self.request, "Changes saved.")
        return super().form_valid(form)


class MatchCancelView(OrganizerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk, organizer=request.user)
        match.status = Match.Status.CANCELLED
        match.save(update_fields=["status"])
        messages.success(request, f"The match at {match.pitch.name} was cancelled.")
        return redirect("my_matches")


class RemovePlayerView(OrganizerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        participation = get_object_or_404(
            Participation.objects.select_related("match", "player"),
            pk=pk,
            match__organizer=request.user,
        )
        match = participation.match
        username = participation.player.username

        participation.delete()
        match.refresh_status()
        match.save(update_fields=["status"])

        messages.success(request, f"{username} was removed from the match.")
        return redirect("match_detail", pk=match.pk)
    
# ---------- Player views ----------


class JoinMatchView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        try:
            join_match(match.pk, request.user)
        except MatchActionError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "You're in! See you on the pitch.")
        return redirect("match_detail", pk=match.pk)


class LeaveMatchView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        try:
            leave_match(match.pk, request.user)
        except MatchActionError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "You left the match. Your spot is open again.")
        return redirect("match_detail", pk=match.pk)


class JoinedMatchesView(LoginRequiredMixin, ListView):
    template_name = "matches/joined_matches.html"
    context_object_name = "matches"

    def get_queryset(self):
        return (
            Match.objects.filter(participations__player=self.request.user)
            .select_related("pitch", "organizer")
            .prefetch_related("participations")
            .order_by("start_time")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        upcoming, past = [], []
        for match in context["matches"]:
            is_upcoming = match.start_time > now and match.status != Match.Status.CANCELLED
            (upcoming if is_upcoming else past).append(match)
        context["upcoming"] = upcoming
        context["past"] = list(reversed(past))
        return context