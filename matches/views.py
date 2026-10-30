from django.db.models import Count, Prefetch
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, TemplateView

from .models import Match, Pitch


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
                    "start": timezone.localtime(match.start_time).strftime("%a %d %b, %H:%M"),
                    "spots_left": max(match.spots_needed - match.taken, 0),
                    "spots_needed": match.spots_needed,
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