from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone

from matches.models import Match, MatchExit, Participation
from matches.services import MatchActionError, can_use_chat, join_match, leave_match, remove_player

from .utils import make_match, make_organizer, make_user


class JoinMatchTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.player = make_user("player")
        self.match = make_match(self.organizer, spots_needed=2)

    def test_player_can_join(self):
        join_match(self.match.pk, self.player)
        self.assertTrue(Participation.objects.filter(match=self.match, player=self.player).exists())

    def test_cannot_join_twice(self):
        join_match(self.match.pk, self.player)
        with self.assertRaisesMessage(MatchActionError, "already have a spot"):
            join_match(self.match.pk, self.player)
        self.assertEqual(self.match.participations.count(), 1)

    def test_cannot_join_when_full(self):
        join_match(self.match.pk, make_user("first"))
        join_match(self.match.pk, make_user("second"))
        with self.assertRaisesMessage(MatchActionError, "last spot was just taken"):
            join_match(self.match.pk, self.player)
        self.assertEqual(self.match.participations.count(), 2)

    def test_last_spot_marks_match_full(self):
        join_match(self.match.pk, make_user("first"))
        join_match(self.match.pk, self.player)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.FULL)

    def test_cannot_join_started_match(self):
        started = make_match(self.organizer, starts_in=-timedelta(minutes=5))
        with self.assertRaisesMessage(MatchActionError, "already started"):
            join_match(started.pk, self.player)

    def test_cannot_join_cancelled_match(self):
        self.match.status = Match.Status.CANCELLED
        self.match.save()
        with self.assertRaisesMessage(MatchActionError, "cancelled"):
            join_match(self.match.pk, self.player)

    def test_organizer_cannot_join_own_match(self):
        with self.assertRaisesMessage(MatchActionError, "You organize this match"):
            join_match(self.match.pk, self.organizer)


class LeaveMatchTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.player = make_user("player")
        self.match = make_match(self.organizer)

    def test_leaving_frees_the_spot_and_reopens_a_full_match(self):
        match = make_match(self.organizer, spots_needed=1)
        join_match(match.pk, self.player)
        leave_match(match.pk, self.player)
        match.refresh_from_db()
        self.assertEqual(match.participations.count(), 0)
        self.assertEqual(match.status, Match.Status.ACTIVE)

    def test_cannot_leave_less_than_two_hours_before_kickoff(self):
        match = make_match(self.organizer, starts_in=timedelta(hours=1))
        join_match(match.pk, self.player)
        with self.assertRaisesMessage(MatchActionError, "up to 2 hours before"):
            leave_match(match.pk, self.player)

    def test_cannot_leave_a_match_you_are_not_in(self):
        with self.assertRaisesMessage(MatchActionError, "don't have a spot"):
            leave_match(self.match.pk, self.player)

    def test_cannot_rejoin_within_an_hour_of_leaving(self):
        join_match(self.match.pk, self.player)
        leave_match(self.match.pk, self.player)
        with self.assertRaisesMessage(MatchActionError, "join again in"):
            join_match(self.match.pk, self.player)

    def test_can_rejoin_after_the_cooldown(self):
        join_match(self.match.pk, self.player)
        leave_match(self.match.pk, self.player)
        # Pretend the player left 61 minutes ago
        MatchExit.objects.filter(match=self.match, player=self.player).update(
            created_at=timezone.now() - timedelta(minutes=61)
        )
        join_match(self.match.pk, self.player)
        self.assertTrue(Participation.objects.filter(match=self.match, player=self.player).exists())

    def test_cooldown_applies_only_to_the_same_match(self):
        other_match = make_match(self.organizer)
        join_match(self.match.pk, self.player)
        leave_match(self.match.pk, self.player)
        join_match(other_match.pk, self.player)
        self.assertTrue(Participation.objects.filter(match=other_match, player=self.player).exists())


class RemovePlayerTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.player = make_user("player")
        self.match = make_match(self.organizer)
        join_match(self.match.pk, self.player)
        self.participation = Participation.objects.get(match=self.match, player=self.player)

    def test_removed_player_can_never_rejoin(self):
        remove_player(self.participation.pk, self.organizer)
        with self.assertRaisesMessage(MatchActionError, "removed you"):
            join_match(self.match.pk, self.player)

        # Still blocked long after the 1-hour cooldown would have ended
        MatchExit.objects.update(created_at=timezone.now() - timedelta(days=1))
        with self.assertRaisesMessage(MatchActionError, "removed you"):
            join_match(self.match.pk, self.player)

    def test_another_organizer_cannot_remove_players(self):
        with self.assertRaises(Participation.DoesNotExist):
            remove_player(self.participation.pk, make_organizer("other"))
        self.assertTrue(Participation.objects.filter(pk=self.participation.pk).exists())


class ChatAccessTests(TestCase):
    def test_only_the_organizer_and_joined_players_can_use_the_chat(self):
        organizer = make_organizer()
        player = make_user("player")
        outsider = make_user("outsider")
        match = make_match(organizer)
        join_match(match.pk, player)

        self.assertTrue(can_use_chat(match, organizer))
        self.assertTrue(can_use_chat(match, player))
        self.assertFalse(can_use_chat(match, outsider))
        self.assertFalse(can_use_chat(match, AnonymousUser()))