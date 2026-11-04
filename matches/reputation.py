"""Reputation: players rate each other after a match, and organizers mark who didn't turn up."""

from datetime import timedelta

from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from .models import Match, Participation, PlayerRating

# Ratings and no-shows can be changed until this long after the final whistle
FEEDBACK_WINDOW = timedelta(hours=48)


class ReputationError(Exception):
    """Raised when a rating or a no-show can't be saved. The message is shown to the user."""


def feedback_open(match, now=None):
    """True from the end of the match until FEEDBACK_WINDOW later, if the match was played."""
    now = now or timezone.now()
    return match.status != Match.Status.CANCELLED and match.end_time <= now <= match.end_time + FEEDBACK_WINDOW


def played(match):
    """Everyone who was really on the pitch: the organizer and the players who turned up."""
    players = [p.player for p in match.participations.all() if not p.no_show]
    return [match.organizer, *players]


def teammates_to_rate(match, user):
    """The people `user` can rate in this match, each with the stars `user` already gave (or None)."""
    if not user.is_authenticated or not feedback_open(match):
        return []
    people = played(match)
    if user not in people:
        return []
    given = dict(match.ratings.filter(rater=user).values_list("rated_id", "stars"))
    return [(person, given.get(person.id)) for person in people if person != user]


def rate_player(match_id, rater, rated_id, stars):
    """Save `rater`'s rating of a teammate. Rating the same teammate again changes the rating."""
    match = Match.objects.select_related("organizer").prefetch_related("participations__player").get(pk=match_id)

    if stars not in range(1, 6):
        raise ReputationError("Pick from 1 to 5 stars.")
    if not feedback_open(match):
        raise ReputationError("Ratings are open for 48 hours after the match ends.")

    people = {person.id: person for person in played(match)}
    if rater.id not in people:
        raise ReputationError("Only the people who played this match can rate it.")
    if rated_id == rater.id:
        raise ReputationError("You can't rate yourself.")
    if rated_id not in people:
        raise ReputationError("This player didn't play in this match.")

    rating, _ = PlayerRating.objects.update_or_create(
        match=match,
        rater=rater,
        rated=people[rated_id],
        defaults={"stars": stars},
    )
    return rating


def set_no_show(participation_id, organizer, no_show):
    """The organizer marks a player as not turned up (or takes the mark back).

    A player who didn't turn up can't rate or be rated, so their ratings in this match are removed.
    Raises Participation.DoesNotExist if the spot isn't in one of the organizer's matches.
    """
    with transaction.atomic():
        participation = (
            Participation.objects.select_for_update(of=("self",))
            .select_related("match", "player")
            .get(pk=participation_id, match__organizer=organizer)
        )
        match = participation.match
        now = timezone.now()
        if match.status == Match.Status.CANCELLED:
            raise ReputationError("This match was cancelled.")
        if not match.has_started:
            raise ReputationError("You can mark no-shows once the match has started.")
        if now > match.end_time + FEEDBACK_WINDOW:
            raise ReputationError("No-shows can be marked up to 48 hours after the match ends.")

        participation.no_show = no_show
        participation.save(update_fields=["no_show"])
        if no_show:
            match.ratings.filter(Q(rater=participation.player) | Q(rated=participation.player)).delete()

    return participation


def matches_played(user, now=None):
    """Matches that took place with `user` on the pitch, as the organizer or a player who turned up."""
    now = now or timezone.now()
    return (
        Match.objects.filter(start_time__lte=now)
        .exclude(status=Match.Status.CANCELLED)
        .filter(Q(organizer=user) | Q(participations__player=user, participations__no_show=False))
        .distinct()
    )


def reputation(user, now=None):
    """The numbers on a profile: matches played, no-shows, how reliable, average rating."""
    now = now or timezone.now()
    # Matches that have started and weren't cancelled
    taken_place = Q(match__start_time__lte=now) & ~Q(match__status=Match.Status.CANCELLED)

    spots = Participation.objects.filter(taken_place, player=user)
    counts = spots.aggregate(total=Count("id"), no_shows=Count("id", filter=Q(no_show=True)))
    showed_up = counts["total"] - counts["no_shows"]

    ratings = PlayerRating.objects.filter(rated=user).aggregate(average=Avg("stars"), count=Count("id"))

    return {
        "matches_played": matches_played(user, now).count(),
        "spots_taken": counts["total"],
        "showed_up": showed_up,
        "no_shows": counts["no_shows"],
        # None until the player has taken a spot in a match that happened
        "reliability": round(100 * showed_up / counts["total"]) if counts["total"] else None,
        "rating_average": round(ratings["average"], 1) if ratings["average"] is not None else None,
        "rating_count": ratings["count"],
    }
