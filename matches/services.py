from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Match, Participation

# Players can give up their spot until this long before kick-off
LEAVE_DEADLINE = timedelta(hours=2)


class MatchActionError(Exception):
    """Raised when a player can't join or leave a match. The message is shown to the user."""


def join_match(match_id, player):
    """Give `player` a spot in the match, safely, even if many players click at once."""
    with transaction.atomic():
        # Lock this match row until the transaction ends.
        # If another player is joining the same match at the same moment,
        # the database makes them wait here, then they see the updated player count.
        match = Match.objects.select_for_update().get(pk=match_id)

        if match.status == Match.Status.CANCELLED:
            raise MatchActionError("This match was cancelled.")
        if match.has_started:
            raise MatchActionError("This match has already started.")
        if match.organizer_id == player.id:
            raise MatchActionError("You organize this match, so you already have your place.")
        if match.participations.filter(player=player).exists():
            raise MatchActionError("You already have a spot in this match.")
        if match.is_full:
            raise MatchActionError("The last spot was just taken. Try another match.")

        try:
            Participation.objects.create(match=match, player=player)
        except IntegrityError:
            # Backup: the unique constraint in the database caught a duplicate
            raise MatchActionError("You already have a spot in this match.")

        match.refresh_status()
        match.save(update_fields=["status"])

    return match


def leave_match(match_id, player):
    """Remove `player` from the match, if it's not too close to kick-off."""
    with transaction.atomic():
        match = Match.objects.select_for_update().get(pk=match_id)

        participation = match.participations.filter(player=player).first()
        if participation is None:
            raise MatchActionError("You don't have a spot in this match.")

        too_late = timezone.now() > match.start_time - LEAVE_DEADLINE
        if match.status != Match.Status.CANCELLED and too_late:
            raise MatchActionError(
                "You can leave up to 2 hours before kick-off. Contact the organizer if you can't make it."
            )

        participation.delete()
        match.refresh_status()
        match.save(update_fields=["status"])

    return match