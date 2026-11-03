import threading
from datetime import timedelta

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from matches.models import Match, Participation, WaitlistEntry
from matches.services import (
    MatchActionError,
    join_match,
    join_waitlist,
    leave_match,
    leave_waitlist,
    remove_player,
    waitlist_position,
)

from .utils import make_match, make_organizer, make_user


def waiting_usernames(match):
    return list(match.waitlist.values_list("player__username", flat=True))


def joined_usernames(match):
    return sorted(match.participations.values_list("player__username", flat=True))


class FullMatchMixin:
    """A match with 2 spots, both taken by p1 and p2."""

    def setUp(self):
        self.organizer = make_organizer()
        self.match = make_match(self.organizer, spots_needed=2)
        self.p1 = make_user("p1")
        self.p2 = make_user("p2")
        join_match(self.match.pk, self.p1)
        join_match(self.match.pk, self.p2)


class JoinWaitlistTests(FullMatchMixin, TestCase):
    def test_player_can_join_the_waitlist_of_a_full_match(self):
        waiter = make_user("waiter")
        join_waitlist(self.match.pk, waiter)
        self.assertEqual(waiting_usernames(self.match), ["waiter"])

    def test_no_waitlist_while_there_is_a_free_spot(self):
        leave_match(self.match.pk, self.p1)
        with self.assertRaisesMessage(MatchActionError, "join the match right away"):
            join_waitlist(self.match.pk, make_user("waiter"))

    def test_cannot_join_the_waitlist_twice(self):
        waiter = make_user("waiter")
        join_waitlist(self.match.pk, waiter)
        with self.assertRaisesMessage(MatchActionError, "already on the waitlist"):
            join_waitlist(self.match.pk, waiter)

    def test_players_in_the_match_and_the_organizer_cannot_wait(self):
        with self.assertRaisesMessage(MatchActionError, "already have a spot"):
            join_waitlist(self.match.pk, self.p1)
        with self.assertRaisesMessage(MatchActionError, "You organize this match"):
            join_waitlist(self.match.pk, self.organizer)

    def test_removed_player_cannot_come_back_through_the_waitlist(self):
        participation = Participation.objects.get(match=self.match, player=self.p1)
        remove_player(participation.pk, self.organizer)
        join_match(self.match.pk, make_user("new"))  # the match is full again

        with self.assertRaisesMessage(MatchActionError, "removed you"):
            join_waitlist(self.match.pk, self.p1)

    def test_no_waitlist_for_a_cancelled_match(self):
        Match.objects.filter(pk=self.match.pk).update(status=Match.Status.CANCELLED)
        with self.assertRaisesMessage(MatchActionError, "cancelled"):
            join_waitlist(self.match.pk, make_user("waiter"))

    def test_leaving_the_waitlist(self):
        waiter = make_user("waiter")
        join_waitlist(self.match.pk, waiter)
        leave_waitlist(self.match.pk, waiter)
        self.assertEqual(waiting_usernames(self.match), [])

        with self.assertRaisesMessage(MatchActionError, "not on the waitlist"):
            leave_waitlist(self.match.pk, waiter)


class PromotionTests(FullMatchMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.first = make_user("first")
        self.second = make_user("second")
        join_waitlist(self.match.pk, self.first)
        join_waitlist(self.match.pk, self.second)

    def test_first_in_line_gets_the_spot_when_a_player_leaves(self):
        promoted = leave_match(self.match.pk, self.p1)

        self.assertEqual(promoted, [self.first])
        self.assertEqual(joined_usernames(self.match), ["first", "p2"])
        self.assertEqual(waiting_usernames(self.match), ["second"])
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.FULL)

    def test_first_in_line_gets_the_spot_when_a_player_is_removed(self):
        participation = Participation.objects.get(match=self.match, player=self.p2)
        _, _, promoted = remove_player(participation.pk, self.organizer)

        self.assertEqual(promoted, [self.first])
        self.assertEqual(joined_usernames(self.match), ["first", "p1"])

    def test_positions_move_up_as_the_line_shrinks(self):
        self.assertEqual(waitlist_position(self.match, self.first), 1)
        self.assertEqual(waitlist_position(self.match, self.second), 2)

        leave_match(self.match.pk, self.p1)

        self.assertIsNone(waitlist_position(self.match, self.first))
        self.assertEqual(waitlist_position(self.match, self.second), 1)

    def test_nobody_moves_up_after_kick_off(self):
        Match.objects.filter(pk=self.match.pk).update(start_time=timezone.now() - timedelta(minutes=10))
        participation = Participation.objects.get(match=self.match, player=self.p1)
        _, _, promoted = remove_player(participation.pk, self.organizer)

        self.assertEqual(promoted, [])
        self.assertEqual(waiting_usernames(self.match), ["first", "second"])

    def test_more_spots_from_the_organizer_go_to_the_waitlist(self):
        self.client.force_login(self.organizer)
        local_start = timezone.localtime(self.match.start_time)
        response = self.client.post(reverse("match_update", args=[self.match.pk]), {
            "pitch": self.match.pitch.pk,
            "start_time": local_start.strftime("%Y-%m-%dT%H:%M"),
            "duration_minutes": 60,
            "spots_needed": 4,
            "price_per_player": "0",
            "description": "",
        }, follow=True)

        self.assertContains(response, "first and second moved up from the waitlist")
        self.assertEqual(joined_usernames(self.match), ["first", "p1", "p2", "second"])
        self.assertEqual(waiting_usernames(self.match), [])


class WaitlistPageTests(FullMatchMixin, TestCase):
    def test_full_match_offers_the_waitlist_and_shows_your_place(self):
        waiter = make_user("waiter")
        self.client.force_login(waiter)
        url = reverse("match_detail", args=[self.match.pk])
        self.assertContains(self.client.get(url), "Join waitlist")

        response = self.client.post(reverse("waitlist_join", args=[self.match.pk]), follow=True)
        self.assertContains(response, "You&#x27;re on the waitlist")
        self.assertContains(response, "#1 on the waitlist")
        self.assertContains(response, "Leave waitlist")

        joined_page = self.client.get(reverse("joined_matches"))
        self.assertContains(joined_page, "#1 in line")

    def test_leaving_player_hears_who_took_the_spot(self):
        join_waitlist(self.match.pk, make_user("waiter"))
        self.client.force_login(self.p1)
        response = self.client.post(reverse("match_leave", args=[self.match.pk]), follow=True)
        self.assertContains(response, "waiter from the waitlist took your spot")

    def test_waitlist_actions_reject_get_requests(self):
        self.client.force_login(make_user("waiter"))
        for name in ["waitlist_join", "waitlist_leave"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.match.pk])).status_code, 405)


class ConcurrentWaitlistTests(TransactionTestCase):
    """Players leave a full match at the same moment that others try to grab the freed spots."""

    def run_together(self, actions):
        """Start every action at the same moment, each in its own thread and database connection."""
        start_together = threading.Barrier(len(actions))
        results = {}
        results_lock = threading.Lock()

        def run(name, action):
            start_together.wait()
            try:
                action()
                outcome = "ok"
            except MatchActionError:
                outcome = "rejected"
            except Exception as error:
                outcome = f"error: {error!r}"
            finally:
                connection.close()
            with results_lock:
                results[name] = outcome

        threads = [threading.Thread(target=run, args=item) for item in actions.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def test_freed_spots_go_to_the_waitlist_not_to_whoever_clicks_first(self):
        organizer = make_organizer()
        match = make_match(organizer, spots_needed=2)
        p1, p2 = make_user("p1"), make_user("p2")
        join_match(match.pk, p1)
        join_match(match.pk, p2)
        for name in ["w1", "w2", "w3"]:
            join_waitlist(match.pk, make_user(name))
        outsider = make_user("outsider")

        results = self.run_together({
            "p1 leaves": lambda: leave_match(match.pk, p1),
            "p2 leaves": lambda: leave_match(match.pk, p2),
            "outsider joins": lambda: join_match(match.pk, outsider),
        })

        self.assertEqual(results, {"p1 leaves": "ok", "p2 leaves": "ok", "outsider joins": "rejected"})
        self.assertEqual(joined_usernames(match), ["w1", "w2"])
        self.assertEqual(waiting_usernames(match), ["w3"])
        self.assertEqual(WaitlistEntry.objects.count(), 1)
