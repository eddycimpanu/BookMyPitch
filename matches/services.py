import math
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Match, MatchExit, Participation

# Players can give up their spot until this long before kick-off
LEAVE_DEADLINE = timedelta(hours=2)

# After leaving a match, a player must wait this long before joining the same match again
REJOIN_COOLDOWN = timedelta(hours=1)


class MatchActionError(Exception):
    """Raised when a player can't join or leave a match. The message is shown to the user."""


def rejoin_block_reason(match, player):
    """Return why `player` can't join `match` because of an earlier exit, or None if they can."""
    exits = MatchExit.objects.filter(match=match, player=player)

    if exits.filter(reason=MatchExit.Reason.REMOVED).exists():
        return "The organizer removed you from this match, so you can't join it again."

    last_leave = exits.filter(reason=MatchExit.Reason.LEFT).order_by("-created_at").first()
    if last_leave:
        remaining = last_leave.created_at + REJOIN_COOLDOWN - timezone.now()
        if remaining > timedelta(0):
            minutes = max(1, math.ceil(remaining.total_seconds() / 60))
            return f"You left this match recently. You can join again in {minutes} min."

    return None


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

        block_reason = rejoin_block_reason(match, player)
        if block_reason:
            raise MatchActionError(block_reason)

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
        MatchExit.objects.create(match=match, player=player, reason=MatchExit.Reason.LEFT)

        match.refresh_status()
        match.save(update_fields=["status"])

    return match


def remove_player(participation_id, organizer):
    """Organizer removes a player from one of their matches. The player can't rejoin it.

    Raises Participation.DoesNotExist if the spot doesn't exist or the match isn't the organizer's.
    """
    with transaction.atomic():
        participation = Participation.objects.select_related("player").get(
            pk=participation_id,
            match__organizer=organizer,
        )
        match = Match.objects.select_for_update().get(pk=participation.match_id)
        player = participation.player

        participation.delete()
        MatchExit.objects.create(match=match, player=player, reason=MatchExit.Reason.REMOVED)

        match.refresh_status()
        match.save(update_fields=["status"])

    return player, match


def can_use_chat(match, user):
    """Only the organizer and the players who currently have a spot can read and write the chat."""
    if not user.is_authenticated:
        return False
    if match.organizer_id == user.id:
        return True
    return match.participations.filter(player=user).exists()