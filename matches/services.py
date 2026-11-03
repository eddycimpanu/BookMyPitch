import math
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Match, MatchExit, Participation, WaitlistEntry
from .notifications import notify_match_full, notify_promoted

# Players can give up their spot until this long before kick-off
LEAVE_DEADLINE = timedelta(hours=2)

# After leaving a match, a player must wait this long before joining the same match again
REJOIN_COOLDOWN = timedelta(hours=1)


class MatchActionError(Exception):
    """Raised when a player can't join or leave a match. The message is shown to the user."""


def update_status(match):
    """Save the status after the lineup changed. The organizer gets an email when the match fills up."""
    was_full = match.status == Match.Status.FULL
    match.refresh_status()
    match.save(update_fields=["status"])
    if match.status == Match.Status.FULL and not was_full:
        notify_match_full(match)


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

        # If they were also waiting in line, they don't need to any more
        WaitlistEntry.objects.filter(match=match, player=player).delete()

        update_status(match)

    return match


def leave_match(match_id, player):
    """Remove `player` from the match, if it's not too close to kick-off.

    Returns the players from the waitlist who got the freed spot (an empty list if nobody was waiting).
    """
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

        # Still inside the same transaction and the same lock:
        # nobody can grab the freed spot before the first player in line gets it.
        promoted = promote_from_waitlist(match)

        update_status(match)

    return promoted


def remove_player(participation_id, organizer):
    """Organizer removes a player from one of their matches. The player can't rejoin it.

    Returns (removed player, match, players promoted from the waitlist).
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
        promoted = promote_from_waitlist(match)

        update_status(match)

    return player, match, promoted


# ---------- Waitlist ----------


def join_waitlist(match_id, player):
    """Put `player` at the end of the line for a full match."""
    with transaction.atomic():
        # Same lock as join_match, so the match can't change between the checks and the insert
        match = Match.objects.select_for_update().get(pk=match_id)

        if match.status == Match.Status.CANCELLED:
            raise MatchActionError("This match was cancelled.")
        if match.has_started:
            raise MatchActionError("This match has already started.")
        if match.organizer_id == player.id:
            raise MatchActionError("You organize this match, so you already have your place.")
        if match.participations.filter(player=player).exists():
            raise MatchActionError("You already have a spot in this match.")
        if match.waitlist.filter(player=player).exists():
            raise MatchActionError("You're already on the waitlist.")

        block_reason = rejoin_block_reason(match, player)
        if block_reason:
            raise MatchActionError(block_reason)

        if not match.is_full:
            raise MatchActionError("A spot is free, so you can join the match right away.")

        try:
            WaitlistEntry.objects.create(match=match, player=player)
        except IntegrityError:
            raise MatchActionError("You're already on the waitlist.")

    return match


def leave_waitlist(match_id, player):
    """Take `player` off the waitlist. Allowed at any time."""
    with transaction.atomic():
        match = Match.objects.select_for_update().get(pk=match_id)
        deleted, _ = WaitlistEntry.objects.filter(match=match, player=player).delete()
        if not deleted:
            raise MatchActionError("You're not on the waitlist for this match.")

    return match


def promote_from_waitlist(match):
    """Give the free spots to the players waiting in line, first come, first served.

    Must be called inside a transaction that already holds the lock on `match`
    (select_for_update), so two promotions can't hand out the same spot.
    Returns the players who got a spot.
    """
    if match.status == Match.Status.CANCELLED or match.has_started:
        return []

    promoted = []
    for entry in match.waitlist.select_related("player"):
        if match.is_full:
            break

        block_reason = rejoin_block_reason(match, entry.player)
        if block_reason:
            # Someone the organizer removed never comes back through the waitlist
            if match.exits.filter(player=entry.player, reason=MatchExit.Reason.REMOVED).exists():
                entry.delete()
            continue

        Participation.objects.create(match=match, player=entry.player)
        entry.delete()
        promoted.append(entry.player)

    if promoted:
        notify_promoted(match, promoted)
    return promoted


def fill_open_spots(match_id):
    """The organizer added spots to a match: move players up from the waitlist to fill them."""
    with transaction.atomic():
        match = Match.objects.select_for_update().get(pk=match_id)
        promoted = promote_from_waitlist(match)
        update_status(match)

    return promoted


def waitlist_position(match, player):
    """1 for the first player in line, 2 for the next one... None if `player` isn't waiting."""
    waiting = [entry.player_id for entry in match.waitlist.all()]
    return waiting.index(player.id) + 1 if player.id in waiting else None


def can_use_chat(match, user):
    """Only the organizer and the players who currently have a spot can read and write the chat."""
    if not user.is_authenticated:
        return False
    if match.organizer_id == user.id:
        return True
    return match.participations.filter(player=user).exists()
