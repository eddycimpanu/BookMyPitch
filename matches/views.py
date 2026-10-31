from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from accounts.mixins import OrganizerRequiredMixin

from .forms import MatchForm, PitchForm
from .models import ChatMessage, Match, Participation, Pitch
from .services import (
    LEAVE_DEADLINE,
    MatchActionError,
    can_use_chat,
    join_match,
    leave_match,
    rejoin_block_reason,
    remove_player,
)

def day_label(value):
    """Return 'Today', 'Tomorrow' or a date like 'Saturday 1 November', in local time."""
    day = timezone.localdate(value)
    today = timezone.localdate()
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return f"{day:%A} {day.day} {day:%B}"


def weekend_dates(today):
    """The coming Saturday and Sunday. If today is Sunday, only today."""
    if today.weekday() == 6:
        return {today}
    saturday = today + timedelta(days=5 - today.weekday())
    return {saturday, saturday + timedelta(days=1)}


class HomeView(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sectors"] = Pitch.Sector.choices
        return context


def pitches_api(request):
    """Return pitches that have upcoming, non-cancelled matches, as JSON for the map."""
    now = timezone.now()
    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    weekend = weekend_dates(today)
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
        matches = []
        for match in pitch.upcoming_matches:
            match_day = timezone.localdate(match.start_time)
            matches.append({
                "id": match.id,
                "start_iso": match.start_time.isoformat(),
                "day": day_label(match.start_time),
                "time": timezone.localtime(match.start_time).strftime("%H:%M"),
                "is_today": match_day == today,
                "is_tomorrow": match_day == tomorrow,
                "is_weekend": match_day in weekend,
                "spots_needed": match.spots_needed,
                "spots_taken": min(match.taken, match.spots_needed),
                "spots_left": max(match.spots_needed - match.taken, 0),
                "price": str(match.price_per_player),
                "url": reverse("match_detail", args=[match.pk]),
            })

        data.append({
            "id": pitch.id,
            "name": pitch.name,
            "address": pitch.address,
            "sector": pitch.get_sector_display(),
            "sector_id": pitch.sector,
            "lat": float(pitch.latitude),
            "lng": float(pitch.longitude),
            "matches": matches,
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
        could_join = is_open and not is_joined and not match.is_full and user != match.organizer

        block_reason = None
        if could_join and user.is_authenticated:
            block_reason = rejoin_block_reason(match, user)

        context.update({
            "open_slots": range(match.spots_left),
            "is_joined": is_joined,
            "can_join": could_join and block_reason is None,
            "join_block_reason": block_reason,
            "can_leave": is_joined and timezone.now() <= match.start_time - LEAVE_DEADLINE,
            "can_chat": can_use_chat(match, user),
        })
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
        try:
            player, match = remove_player(pk, request.user)
        except Participation.DoesNotExist:
            raise Http404("This player is not in one of your matches.")

        messages.success(request, f"{player.username} was removed and can't rejoin this match.")
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
    
# ---------- Team chat ----------

CHAT_MESSAGE_MAX_LENGTH = 1000
CHAT_HISTORY_LIMIT = 100


def serialize_message(message, user, match):
    return {
        "id": message.id,
        "author": message.author.username,
        "is_me": message.author_id == user.id,
        "is_organizer": message.author_id == match.organizer_id,
        "body": message.body,
        "time": timezone.localtime(message.created_at).strftime("%d %b, %H:%M"),
    }


class MatchChatView(LoginRequiredMixin, View):
    """GET returns messages newer than ?after=<id>. POST sends a message. Team members only."""

    http_method_names = ["get", "post"]

    def _get_match(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        if not can_use_chat(match, request.user):
            denied = JsonResponse(
                {"error": "Only the organizer and players in this match can use the chat."},
                status=403,
            )
            return match, denied
        return match, None

    def get(self, request, pk):
        match, denied = self._get_match(request, pk)
        if denied:
            return denied

        after = request.GET.get("after", "")
        chat_messages = match.messages.select_related("author")

        if after.isdigit() and int(after) > 0:
            chat_messages = list(chat_messages.filter(id__gt=int(after)).order_by("id"))
        else:
            # First load: the latest messages, oldest first
            chat_messages = list(chat_messages.order_by("-id")[:CHAT_HISTORY_LIMIT])[::-1]

        return JsonResponse({
            "messages": [serialize_message(message, request.user, match) for message in chat_messages],
        })

    def post(self, request, pk):
        match, denied = self._get_match(request, pk)
        if denied:
            return denied

        body = request.POST.get("body", "").strip()
        if not body:
            return JsonResponse({"error": "Write a message first."}, status=400)
        if len(body) > CHAT_MESSAGE_MAX_LENGTH:
            return JsonResponse(
                {"error": f"Messages can be up to {CHAT_MESSAGE_MAX_LENGTH} characters."},
                status=400,
            )

        message = ChatMessage.objects.create(match=match, author=request.user, body=body)
        return JsonResponse({"message": serialize_message(message, request.user, match)}, status=201)