from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from accounts.mixins import OrganizerRequiredMixin, OwnerRequiredMixin

from .bookings import (
    BookingError,
    block_time,
    book_pitch,
    booking_days,
    booking_price,
    cancel_booking,
    free_start_times,
    release_booking,
)
from .forms import (
    BlockTimeForm,
    BookedMatchForm,
    BookingForm,
    MatchForm,
    OwnerPitchForm,
    PitchForm,
    WeeklyHoursForm,
)
from .models import BlockedTime, Booking, ChatMessage, Match, OpeningHours, Participation, Pitch, WaitlistEntry
from .notifications import notify_match_cancelled
from .reputation import FEEDBACK_WINDOW, ReputationError, feedback_open, rate_player, set_no_show, teammates_to_rate
from .services import (
    LEAVE_DEADLINE,
    MatchActionError,
    can_use_chat,
    fill_open_spots,
    join_match,
    join_waitlist,
    leave_match,
    leave_waitlist,
    rejoin_block_reason,
    remove_player,
    waitlist_position,
)


def player_names(players):
    """Usernames as text, like 'ana' or 'ana and mihai'. Used in the messages about the waitlist."""
    usernames = [player.username for player in players]
    if len(usernames) == 1:
        return usernames[0]
    return ", ".join(usernames[:-1]) + " and " + usernames[-1]


def day_label(value):
    """Return 'Today', 'Tomorrow' or a date like 'Saturday 1 November', in local time."""
    day = timezone.localdate(value)
    today = timezone.localdate()
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return f"{day:%A} {day.day} {day:%B}"


def week_ranges(today):
    """(start, end) of the rest of this week and of next week. Weeks run Monday to Sunday."""
    sunday = today + timedelta(days=6 - today.weekday())
    next_monday = sunday + timedelta(days=1)
    return (today, sunday), (next_monday, next_monday + timedelta(days=6))


class HomeView(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sectors"] = Pitch.Sector.choices
        return context


def pitches_api(request):
    """Return pitches with upcoming, non-cancelled matches, and every bookable pitch, as JSON for the map."""
    now = timezone.now()
    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    (week_start, week_end), (next_week_start, next_week_end) = week_ranges(today)
    visible_statuses = [Match.Status.ACTIVE, Match.Status.FULL]

    upcoming_matches = (
        Match.objects.filter(start_time__gt=now, status__in=visible_statuses)
        .annotate(taken=Count("participations"))
        .order_by("start_time")
    )

    pitches = (
        Pitch.objects.filter(
            Q(matches__start_time__gt=now, matches__status__in=visible_statuses) | Q(owner__isnull=False)
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
                "is_this_week": week_start <= match_day <= week_end,
                "is_next_week": next_week_start <= match_day <= next_week_end,
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
            "bookable": pitch.is_bookable,
            "price_per_hour": str(pitch.price_per_hour) if pitch.is_bookable else None,
            "url": reverse("pitch_detail", args=[pitch.pk]),
            "matches": matches,
        })

    return JsonResponse({"pitches": data})


class MatchDetailView(DetailView):
    model = Match
    template_name = "matches/match_detail.html"
    context_object_name = "match"

    def get_queryset(self):
        return Match.objects.select_related("pitch", "organizer", "booking").prefetch_related(
            "participations__player",
            "waitlist__player",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        match = self.object
        user = self.request.user

        is_joined = user.is_authenticated and any(
            participation.player_id == user.id for participation in match.participations.all()
        )
        position = waitlist_position(match, user) if user.is_authenticated else None
        is_open = match.status != Match.Status.CANCELLED and not match.has_started
        could_join = is_open and not is_joined and user != match.organizer

        block_reason = None
        if could_join and user.is_authenticated:
            block_reason = rejoin_block_reason(match, user)

        context.update({
            "open_slots": range(match.spots_left),
            "is_joined": is_joined,
            "can_join": could_join and not match.is_full and block_reason is None,
            "can_join_waitlist": could_join and match.is_full and block_reason is None and position is None,
            "waitlist_position": position,
            "waitlist": match.waitlist.all() if is_open else [],
            "teammates_to_rate": teammates_to_rate(match, user),
            "star_choices": [5, 4, 3, 2, 1],  # right to left, so the CSS can light up every star before the hovered one
            "can_mark_no_show": (
                user == match.organizer
                and match.status != Match.Status.CANCELLED
                and match.has_started
                and timezone.now() <= match.end_time + FEEDBACK_WINDOW
            ),
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
            .select_related("pitch", "booking")
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
            match.can_rate = feedback_open(match, now)
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

    def get_form_class(self):
        # A booked match keeps the pitch and the time of its booking
        return BookedMatchForm if hasattr(self.object, "booking") else MatchForm

    def form_valid(self, form):
        form.instance.refresh_status()
        response = super().form_valid(form)

        # More spots than before? The players waiting in line get them first.
        promoted = fill_open_spots(self.object.pk)
        message = "Changes saved."
        if promoted:
            message += f" {player_names(promoted)} moved up from the waitlist."
        messages.success(self.request, message)
        return response


class MatchCancelView(OrganizerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk, organizer=request.user)
        with transaction.atomic():
            was_cancelled = match.status == Match.Status.CANCELLED
            match.status = Match.Status.CANCELLED
            match.save(update_fields=["status"])
            released = release_booking(match)
            if not was_cancelled:
                notify_match_cancelled(match)

        message = f"The match at {match.pitch.name} was cancelled."
        if released:
            message += " Your pitch booking was cancelled too, so the time is free again."
        messages.success(request, message)
        return redirect("my_matches")


class RemovePlayerView(OrganizerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        try:
            player, match, promoted = remove_player(pk, request.user)
        except Participation.DoesNotExist:
            raise Http404("This player is not in one of your matches.")

        message = f"{player.username} was removed and can't rejoin this match."
        if promoted:
            message += f" {player_names(promoted)} from the waitlist took the spot."
        messages.success(request, message)
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
            promoted = leave_match(match.pk, request.user)
        except MatchActionError as error:
            messages.error(request, str(error))
        else:
            if promoted:
                messages.success(request, f"You left the match. {player_names(promoted)} from the waitlist took your spot.")
            else:
                messages.success(request, "You left the match. Your spot is open again.")
        return redirect("match_detail", pk=match.pk)


class JoinWaitlistView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        try:
            join_waitlist(match.pk, request.user)
        except MatchActionError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "You're on the waitlist. If a spot opens, it's yours automatically.")
        return redirect("match_detail", pk=match.pk)


class LeaveWaitlistView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        match = get_object_or_404(Match, pk=pk)
        try:
            leave_waitlist(match.pk, request.user)
        except MatchActionError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "You left the waitlist.")
        return redirect("match_detail", pk=match.pk)


class JoinedMatchesView(LoginRequiredMixin, ListView):
    template_name = "matches/joined_matches.html"
    context_object_name = "matches"

    def get_queryset(self):
        return (
            Match.objects.filter(participations__player=self.request.user)
            .select_related("pitch", "organizer", "booking")
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
            match.can_rate = feedback_open(match, now)
        context["upcoming"] = upcoming
        context["past"] = list(reversed(past))

        # Upcoming matches where the player is waiting in line, with their place in the line
        entries = (
            WaitlistEntry.objects.filter(player=self.request.user, match__start_time__gt=now)
            .exclude(match__status=Match.Status.CANCELLED)
            .select_related("match__pitch")
            .prefetch_related("match__waitlist", "match__participations")
            .order_by("match__start_time")
        )
        context["waiting"] = [(entry.match, waitlist_position(entry.match, self.request.user)) for entry in entries]
        return context


# ---------- Ratings and no-shows ----------


class RatePlayerView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, user_pk):
        match = get_object_or_404(Match, pk=pk)
        stars = request.POST.get("stars", "")
        try:
            rating = rate_player(match.pk, request.user, user_pk, int(stars) if stars.isdigit() else 0)
        except ReputationError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"You gave {rating.rated.username} {rating.stars} star{'s' if rating.stars > 1 else ''}.")
        return redirect(reverse("match_detail", args=[match.pk]) + "#rate")


class NoShowView(OrganizerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        no_show = request.POST.get("no_show") == "1"
        try:
            participation = set_no_show(pk, request.user, no_show)
        except Participation.DoesNotExist:
            raise Http404("This player is not in one of your matches.")
        except ReputationError as error:
            participation = get_object_or_404(Participation, pk=pk)
            messages.error(request, str(error))
        else:
            name = participation.player.username
            messages.success(request, f"{name} is marked as a no-show." if no_show else f"{name} is marked as played.")
        return redirect("match_detail", pk=participation.match_id)


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


# ---------- Pitch owners ----------


class PitchDetailView(DetailView):
    model = Pitch
    template_name = "matches/pitch_detail.html"
    context_object_name = "pitch"

    def get_queryset(self):
        return Pitch.objects.select_related("owner").prefetch_related("opening_hours")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pitch = self.object
        hours_by_day = {hours.weekday: hours for hours in pitch.opening_hours.all()}

        context["week"] = [
            (weekday, name, hours_by_day.get(weekday)) for weekday, name in OpeningHours.Weekday.choices
        ]
        context["today_weekday"] = timezone.localdate().weekday()
        context["upcoming_matches"] = (
            pitch.matches.filter(start_time__gt=timezone.now())
            .exclude(status=Match.Status.CANCELLED)
            .order_by("start_time")[:10]
        )
        context["can_book"] = pitch.is_bookable and getattr(self.request.user, "is_organizer", False)
        return context


class OwnerPitchListView(OwnerRequiredMixin, ListView):
    template_name = "matches/owner_pitches.html"
    context_object_name = "pitches"

    def get_queryset(self):
        return Pitch.objects.filter(owner=self.request.user).prefetch_related("opening_hours")


class OwnerPitchFormView(OwnerRequiredMixin, View):
    """List a new bookable pitch, or edit one you own: location, details, price and weekly hours."""

    template_name = "matches/owner_pitch_form.html"

    def get_pitch(self):
        if "pk" not in self.kwargs:
            return None
        # Only your own pitches. Anything else returns 404.
        return get_object_or_404(Pitch, pk=self.kwargs["pk"], owner=self.request.user)

    def get(self, request, pk=None):
        pitch = self.get_pitch()
        return self.render_forms(OwnerPitchForm(instance=pitch), WeeklyHoursForm(pitch=pitch), pitch)

    def post(self, request, pk=None):
        pitch = self.get_pitch()
        pitch_form = OwnerPitchForm(request.POST, instance=pitch)
        hours_form = WeeklyHoursForm(request.POST, pitch=pitch)

        if not (pitch_form.is_valid() and hours_form.is_valid()):
            return self.render_forms(pitch_form, hours_form, pitch)

        with transaction.atomic():
            pitch = pitch_form.save(commit=False)
            pitch.owner = request.user
            if pitch.created_by_id is None:
                pitch.created_by = request.user
            pitch.save()
            hours_form.save(pitch)

        messages.success(request, f"{pitch.name} is saved.")
        return redirect("pitch_detail", pk=pitch.pk)

    def render_forms(self, pitch_form, hours_form, pitch):
        return render(self.request, self.template_name, {
            "form": pitch_form,
            "hours_form": hours_form,
            "pitch": pitch,
        })


# ---------- Booking a pitch ----------


class BookPitchView(OrganizerRequiredMixin, View):
    """Pick a day, a duration and a free start time, then book the pitch and create the match."""

    template_name = "matches/pitch_book.html"

    def load_choices(self, request, pk):
        self.pitch = get_object_or_404(
            Pitch.objects.filter(owner__isnull=False).prefetch_related("opening_hours"),
            pk=pk,
        )
        self.days = booking_days(self.pitch)
        open_days = [day for day, is_open in self.days if is_open]

        self.day = None
        try:
            asked = date.fromisoformat(request.GET.get("date", ""))
            if any(day == asked for day, _ in self.days):
                self.day = asked
        except ValueError:
            pass
        if self.day is None:
            self.day = open_days[0] if open_days else self.days[0][0]

        durations = [value for value, _ in Match.Duration.choices]
        asked_duration = request.GET.get("duration", "")
        self.duration = int(asked_duration) if asked_duration.isdigit() and int(asked_duration) in durations else 60

        self.start_times = free_start_times(self.pitch, self.day, self.duration)

    def get(self, request, pk):
        self.load_choices(request, pk)
        return self.render_page(BookingForm(start_times=self.start_times))

    def post(self, request, pk):
        self.load_choices(request, pk)
        form = BookingForm(request.POST, start_times=self.start_times)
        if not form.is_valid():
            return self.render_page(form)

        try:
            match = book_pitch(
                self.pitch.pk,
                request.user,
                form.cleaned_data["start"],
                self.duration,
                form.cleaned_data["spots_needed"],
                form.cleaned_data["price_per_player"],
                form.cleaned_data["description"],
            )
        except BookingError as error:
            messages.error(request, str(error))
            self.start_times = free_start_times(self.pitch, self.day, self.duration)
            return self.render_page(BookingForm(request.POST, start_times=self.start_times))

        messages.success(request, "Pitch booked. Your match is on the map, now let players join.")
        return redirect("match_detail", pk=match.pk)

    def render_page(self, form):
        return render(self.request, self.template_name, {
            "pitch": self.pitch,
            "form": form,
            "days": self.days,
            "day": self.day,
            "durations": Match.Duration.choices,
            "duration": self.duration,
            "duration_label": Match.Duration(self.duration).label,
            "total_price": booking_price(self.pitch, self.duration),
        })


# ---------- Owner panel: bookings and blocked times ----------


class OwnerBookingsView(OwnerRequiredMixin, View):
    """Upcoming bookings on the owner's pitches, grouped by day, plus the times they blocked."""

    template_name = "matches/owner_bookings.html"

    def get(self, request):
        return self.render_page(BlockTimeForm(owner=request.user, initial=self.form_initial()))

    def post(self, request):
        # Blocking a time is the only form posted to this page
        form = BlockTimeForm(request.POST, owner=request.user)
        if not form.is_valid():
            return self.render_page(form)

        try:
            block_time(
                form.cleaned_data["pitch"].pk,
                request.user,
                form.cleaned_data["start_time"],
                form.cleaned_data["end_time"],
                form.cleaned_data["reason"],
            )
        except BookingError as error:
            form.add_error(None, str(error))
            return self.render_page(form)

        messages.success(request, "Time blocked. Organizers can't book it now.")
        return redirect("owner_bookings")

    def form_initial(self):
        initial = {"day": timezone.localdate() + timedelta(days=1)}
        pitch_id = self.request.GET.get("pitch", "")
        if pitch_id.isdigit():
            initial["pitch"] = pitch_id
        return initial

    def render_page(self, form):
        now = timezone.now()
        owner = self.request.user

        bookings = (
            Booking.objects.filter(pitch__owner=owner, status=Booking.Status.CONFIRMED, end_time__gt=now)
            .select_related("pitch", "match__organizer")
            .annotate(players=Count("match__participations"))
            .order_by("start_time")
        )
        days = []
        for booking in bookings:
            day = timezone.localdate(booking.start_time)
            if not days or days[-1][0] != day:
                days.append((day, []))
            days[-1][1].append(booking)

        return render(self.request, self.template_name, {
            "days": days,
            "blocked_times": BlockedTime.objects.filter(pitch__owner=owner, end_time__gt=now).select_related("pitch"),
            "has_pitches": owner.owned_pitches.exists(),
            "form": form,
        })


class OwnerCancelBookingView(OwnerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        try:
            booking = cancel_booking(pk, request.user, request.POST.get("reason", "")[:200])
        except Booking.DoesNotExist:
            raise Http404("This booking is not on one of your pitches.")
        except BookingError as error:
            messages.error(request, str(error))
        else:
            local_start = timezone.localtime(booking.start_time)
            messages.success(
                request,
                f"Booking on {local_start:%a %d %b, %H:%M} cancelled. The organizer will see it on the match page.",
            )
        return redirect("owner_bookings")


class OwnerUnblockTimeView(OwnerRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        blocked = get_object_or_404(BlockedTime, pk=pk, pitch__owner=request.user)
        blocked.delete()
        messages.success(request, "The time is open for bookings again.")
        return redirect("owner_bookings")
