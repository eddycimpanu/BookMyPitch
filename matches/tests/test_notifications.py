from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from matches.bookings import cancel_booking
from matches.models import Match, Participation
from matches.notifications import send_due_reminders
from matches.services import join_match, join_waitlist, leave_match

from .test_bookings import BookingSetupMixin
from .utils import make_match, make_organizer, make_user


class BrokenMailServer(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise ConnectionRefusedError("mail server is down")


def recipients():
    return sorted(address for email in mail.outbox for address in email.to)


class EmailTestCase(TestCase):
    def run_and_send(self, action, *args):
        """Run the action and the emails it queues for after the commit.

        A TestCase never really commits, so on_commit callbacks only run when we capture them.
        """
        with self.captureOnCommitCallbacks(execute=True):
            return action(*args)


class WaitlistEmailTests(EmailTestCase):
    def test_player_who_moves_up_from_the_waitlist_gets_an_email(self):
        organizer = make_organizer()
        match = make_match(organizer, spots_needed=1)
        leaver, waiter = make_user("leaver"), make_user("waiter")
        join_match(match.pk, leaver)
        join_waitlist(match.pk, waiter)

        self.run_and_send(leave_match, match.pk, leaver)

        self.assertEqual(recipients(), ["waiter@example.com"])
        email = mail.outbox[0]
        self.assertIn("You're in: Test Pitch", email.subject)
        self.assertIn("it's yours now", email.body)
        self.assertIn(f"http://127.0.0.1:8000/matches/{match.pk}/", email.body)


class MatchFullEmailTests(EmailTestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.match = make_match(self.organizer, spots_needed=2)

    def test_organizer_hears_when_the_last_spot_is_taken(self):
        self.run_and_send(join_match, self.match.pk, make_user("ana"))
        self.assertEqual(mail.outbox, [])

        self.run_and_send(join_match, self.match.pk, make_user("mihai"))
        self.assertEqual(recipients(), ["organizer@example.com"])
        self.assertIn("Your match is full", mail.outbox[0].subject)
        self.assertIn("Players: ana, mihai", mail.outbox[0].body)

    def test_no_second_full_email_when_the_waitlist_refills_the_spot(self):
        ana = make_user("ana")
        join_match(self.match.pk, ana)
        join_match(self.match.pk, make_user("mihai"))
        join_waitlist(self.match.pk, make_user("waiter"))

        self.run_and_send(leave_match, self.match.pk, ana)

        # Only the player who moved up gets an email. The match never stopped being full.
        self.assertEqual(recipients(), ["waiter@example.com"])

    def test_no_email_when_the_change_is_rolled_back(self):
        join_match(self.match.pk, make_user("ana"))

        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    join_match(self.match.pk, make_user("mihai"))  # fills the match and queues the email
                    raise RuntimeError("something later in the same transaction failed")
            except RuntimeError:
                pass

        # The join was undone, so the organizer must not hear that the match is full
        self.assertEqual(mail.outbox, [])
        self.assertFalse(self.match.participations.filter(player__username="mihai").exists())

    def test_players_without_an_email_address_are_skipped(self):
        join_match(self.match.pk, make_user("ana"))
        self.organizer.email = ""
        self.organizer.save()

        self.run_and_send(join_match, self.match.pk, make_user("mihai"))
        self.assertEqual(mail.outbox, [])


class CancelEmailTests(BookingSetupMixin, EmailTestCase):
    def test_organizer_cancelling_emails_the_players(self):
        match = make_match(self.organizer)
        join_match(match.pk, make_user("ana"))
        join_match(match.pk, make_user("mihai"))
        self.client.force_login(self.organizer)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("match_cancel", args=[match.pk]))

        self.assertEqual(recipients(), ["ana@example.com", "mihai@example.com"])
        self.assertIn("The organizer cancelled this match", mail.outbox[0].body)

    def test_each_player_gets_their_own_email(self):
        match = make_match(self.organizer)
        join_match(match.pk, make_user("ana"))
        join_match(match.pk, make_user("mihai"))
        self.client.force_login(self.organizer)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("match_cancel", args=[match.pk]))

        self.assertEqual([len(email.to) for email in mail.outbox], [1, 1])

    def test_owner_cancelling_emails_the_players_and_the_organizer_with_the_reason(self):
        match = self.book("20:00")
        join_match(match.pk, make_user("ana"))

        self.run_and_send(cancel_booking, match.booking.pk, self.owner, "Pitch repairs")

        self.assertEqual(recipients(), ["ana@example.com", "organizer@example.com"])
        for email in mail.outbox:
            self.assertIn("The pitch owner cancelled the booking", email.body)
            self.assertIn("Reason: Pitch repairs", email.body)

class ReminderTests(EmailTestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.soon = make_match(self.organizer, starts_in=timedelta(minutes=90))
        join_match(self.soon.pk, make_user("ana"))

    def test_organizer_and_players_get_a_reminder_once(self):
        self.assertEqual(self.run_and_send(send_due_reminders), 1)
        self.assertEqual(recipients(), ["ana@example.com", "organizer@example.com"])
        self.assertIn("Kick-off soon: Test Pitch", mail.outbox[0].subject)

        mail.outbox.clear()
        self.assertEqual(self.run_and_send(send_due_reminders), 0)
        self.assertEqual(mail.outbox, [])

    def test_no_reminder_for_later_cancelled_or_started_matches(self):
        Match.objects.filter(pk=self.soon.pk).update(status=Match.Status.CANCELLED)
        make_match(self.organizer, starts_in=timedelta(hours=5))
        make_match(self.organizer, starts_in=-timedelta(minutes=10))

        self.assertEqual(self.run_and_send(send_due_reminders), 0)
        self.assertEqual(mail.outbox, [])

    def test_command_sends_the_reminders(self):
        out = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("send_match_reminders", stdout=out)

        self.assertIn("Reminders sent for 1 match(es)", out.getvalue())
        self.assertEqual(len(mail.outbox), 2)


@override_settings(MAILERS={"default": {"BACKEND": "matches.tests.test_notifications.BrokenMailServer"}})
class MailServerDownTests(EmailTestCase):
    def test_the_action_still_works_when_emails_cannot_be_sent(self):
        organizer = make_organizer()
        match = make_match(organizer, spots_needed=1)

        with self.assertLogs("matches.notifications", level="ERROR"):
            self.run_and_send(join_match, match.pk, make_user("ana"))

        self.assertTrue(Participation.objects.filter(match=match).exists())
