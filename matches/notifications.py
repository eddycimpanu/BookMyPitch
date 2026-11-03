"""Emails to players and organizers. In development they are printed in the terminal (see MAILERS in settings).

Each email is a text template in templates/emails/. Its first line is the subject, the rest is the body.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage, mailers
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Match

logger = logging.getLogger(__name__)

# The reminder goes out this long before kick-off
REMINDER_BEFORE = timedelta(hours=2)


def match_url(match):
    return settings.SITE_URL.rstrip("/") + reverse("match_detail", args=[match.pk])


def build_email(template, user, context):
    text = render_to_string(f"emails/{template}.txt", {"user": user, **context})
    subject, body = text.strip().split("\n", 1)
    return EmailMessage(subject=subject.strip(), body=body.strip() + "\n", to=[user.email])


def send_after_commit(template, users, match, **context):
    """Send one email per user, after the current transaction is saved.

    One email each, so nobody sees the other players' addresses.
    After the commit, so nobody is told about a change that was rolled back.
    """
    recipients = [user for user in users if user.email]
    if not recipients:
        return
    context.update({"match": match, "url": match_url(match), "site_url": settings.SITE_URL})
    # Built now, while the data is exactly as the email describes it
    emails = [build_email(template, user, context) for user in recipients]
    transaction.on_commit(lambda: deliver(emails))


def deliver(emails):
    try:
        mailers.default.send_messages(emails)
    except Exception:
        # The action itself is already saved. A broken mail server must not turn it into an error page.
        logger.exception("Could not send %d email(s)", len(emails))


def players_of(match):
    return [participation.player for participation in match.participations.select_related("player")]


# ---------- The emails ----------


def notify_promoted(match, players):
    """A spot opened and these players moved up from the waitlist."""
    send_after_commit("waitlist_promoted", players, match)


def notify_match_full(match):
    """Tell the organizer that every spot is taken."""
    send_after_commit("match_full", [match.organizer], match, players=players_of(match))


def notify_match_cancelled(match, by_owner=False, reason=""):
    """Tell the players, and the organizer too if the pitch owner cancelled the booking."""
    recipients = players_of(match)
    if by_owner:
        recipients.append(match.organizer)
    send_after_commit("match_cancelled", recipients, match, by_owner=by_owner, reason=reason)


def send_due_reminders(now=None):
    """Email the organizer and players of every match that kicks off within REMINDER_BEFORE.

    Returns how many matches got a reminder. Safe to run as often as you like:
    each match gets one reminder, even if two runs overlap.
    """
    now = now or timezone.now()
    with transaction.atomic():
        due = (
            Match.objects.filter(
                status__in=[Match.Status.ACTIVE, Match.Status.FULL],
                reminder_sent_at__isnull=True,
                start_time__gt=now,
                start_time__lte=now + REMINDER_BEFORE,
            )
            .select_related("pitch", "organizer")
            # Another run that is busy with a match keeps it. This run skips it instead of sending it again.
            .select_for_update(of=("self",), skip_locked=True)
        )
        count = 0
        for match in due:
            players = players_of(match)
            send_after_commit("match_reminder", [match.organizer, *players], match, players=players)
            match.reminder_sent_at = now
            match.save(update_fields=["reminder_sent_at"])
            count += 1

    return count
