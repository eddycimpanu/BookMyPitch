from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from matches.models import Match, Participation, PlayerRating
from matches.reputation import ReputationError, rate_player, reputation, set_no_show, teammates_to_rate

from .utils import make_match, make_organizer, make_user


class PlayedMatchMixin:
    """A 1-hour match that kicked off 3 hours ago (so it ended 2 hours ago) with ana and mihai."""

    def setUp(self):
        self.organizer = make_organizer()
        self.ana = make_user("ana")
        self.mihai = make_user("mihai")
        self.match = make_match(self.organizer, starts_in=-timedelta(hours=3))
        self.ana_spot = Participation.objects.create(match=self.match, player=self.ana)
        self.mihai_spot = Participation.objects.create(match=self.match, player=self.mihai)

    def move_kick_off(self, starts_in):
        Match.objects.filter(pk=self.match.pk).update(start_time=timezone.now() + starts_in)


class RatingTests(PlayedMatchMixin, TestCase):
    def test_players_rate_each_other_and_the_organizer(self):
        rate_player(self.match.pk, self.ana, self.mihai.pk, 4)
        rate_player(self.match.pk, self.ana, self.organizer.pk, 5)
        rate_player(self.match.pk, self.organizer, self.ana.pk, 3)
        self.assertEqual(PlayerRating.objects.count(), 3)

    def test_rating_again_changes_the_rating(self):
        rate_player(self.match.pk, self.ana, self.mihai.pk, 2)
        rate_player(self.match.pk, self.ana, self.mihai.pk, 5)
        self.assertEqual(list(PlayerRating.objects.values_list("stars", flat=True)), [5])

    def test_ratings_open_only_after_the_match_and_for_48_hours(self):
        self.move_kick_off(-timedelta(minutes=30))  # still playing
        with self.assertRaisesMessage(ReputationError, "48 hours after the match ends"):
            rate_player(self.match.pk, self.ana, self.mihai.pk, 4)

        self.move_kick_off(-timedelta(hours=50))  # ended 49 hours ago
        with self.assertRaisesMessage(ReputationError, "48 hours after the match ends"):
            rate_player(self.match.pk, self.ana, self.mihai.pk, 4)

    def test_only_people_who_played_can_rate_or_be_rated(self):
        stranger = make_user("stranger")
        with self.assertRaisesMessage(ReputationError, "Only the people who played"):
            rate_player(self.match.pk, stranger, self.ana.pk, 5)
        with self.assertRaisesMessage(ReputationError, "didn't play"):
            rate_player(self.match.pk, self.ana, stranger.pk, 1)
        with self.assertRaisesMessage(ReputationError, "rate yourself"):
            rate_player(self.match.pk, self.ana, self.ana.pk, 5)

    def test_stars_must_be_from_1_to_5(self):
        for stars in [0, 6]:
            with self.subTest(stars=stars), self.assertRaisesMessage(ReputationError, "1 to 5"):
                rate_player(self.match.pk, self.ana, self.mihai.pk, stars)

    def test_no_ratings_for_a_cancelled_match(self):
        Match.objects.filter(pk=self.match.pk).update(status=Match.Status.CANCELLED)
        with self.assertRaises(ReputationError):
            rate_player(self.match.pk, self.ana, self.mihai.pk, 4)

    def test_teammates_to_rate_lists_everyone_else_with_the_stars_already_given(self):
        rate_player(self.match.pk, self.ana, self.mihai.pk, 4)
        self.assertEqual(teammates_to_rate(self.match, self.ana), [(self.organizer, None), (self.mihai, 4)])
        self.assertEqual(teammates_to_rate(self.match, make_user("stranger")), [])


class NoShowTests(PlayedMatchMixin, TestCase):
    def test_no_show_cant_rate_or_be_rated_and_loses_their_ratings(self):
        rate_player(self.match.pk, self.ana, self.mihai.pk, 5)
        rate_player(self.match.pk, self.mihai, self.ana.pk, 5)

        set_no_show(self.mihai_spot.pk, self.organizer, True)

        self.assertEqual(PlayerRating.objects.count(), 0)
        with self.assertRaisesMessage(ReputationError, "Only the people who played"):
            rate_player(self.match.pk, self.mihai, self.ana.pk, 5)
        with self.assertRaisesMessage(ReputationError, "didn't play"):
            rate_player(self.match.pk, self.ana, self.mihai.pk, 5)

    def test_the_organizer_can_take_the_mark_back(self):
        set_no_show(self.mihai_spot.pk, self.organizer, True)
        set_no_show(self.mihai_spot.pk, self.organizer, False)
        self.mihai_spot.refresh_from_db()
        self.assertFalse(self.mihai_spot.no_show)

    def test_no_shows_only_after_kick_off(self):
        self.move_kick_off(timedelta(hours=1))
        with self.assertRaisesMessage(ReputationError, "once the match has started"):
            set_no_show(self.mihai_spot.pk, self.organizer, True)

    def test_only_the_organizer_of_the_match_can_mark_no_shows(self):
        with self.assertRaises(Participation.DoesNotExist):
            set_no_show(self.mihai_spot.pk, make_organizer("other"), True)


class ReputationNumbersTests(PlayedMatchMixin, TestCase):
    def test_profile_numbers(self):
        # mihai played this match, then didn't turn up to a second one
        second = make_match(self.organizer, starts_in=-timedelta(hours=5))
        missed = Participation.objects.create(match=second, player=self.mihai)
        set_no_show(missed.pk, self.organizer, True)
        rate_player(self.match.pk, self.ana, self.mihai.pk, 4)
        rate_player(self.match.pk, self.organizer, self.mihai.pk, 5)

        stats = reputation(self.mihai)

        self.assertEqual(stats["matches_played"], 1)
        self.assertEqual(stats["spots_taken"], 2)
        self.assertEqual(stats["no_shows"], 1)
        self.assertEqual(stats["reliability"], 50)
        self.assertEqual(stats["rating_average"], 4.5)
        self.assertEqual(stats["rating_count"], 2)

    def test_upcoming_and_cancelled_matches_dont_count(self):
        Participation.objects.create(match=make_match(self.organizer), player=self.ana)
        cancelled = make_match(self.organizer, starts_in=-timedelta(hours=4), status=Match.Status.CANCELLED)
        Participation.objects.create(match=cancelled, player=self.ana)

        stats = reputation(self.ana)
        self.assertEqual(stats["matches_played"], 1)
        self.assertEqual(stats["spots_taken"], 1)

    def test_the_organizer_counts_their_matches_as_played(self):
        self.assertEqual(reputation(self.organizer)["matches_played"], 1)
        self.assertIsNone(reputation(self.organizer)["reliability"])


class RatingPageTests(PlayedMatchMixin, TestCase):
    def test_player_rates_a_teammate_from_the_match_page(self):
        self.client.force_login(self.ana)
        page = self.client.get(reverse("match_detail", args=[self.match.pk]))
        self.assertContains(page, "Rate your teammates")

        response = self.client.post(
            reverse("rate_player", args=[self.match.pk, self.mihai.pk]), {"stars": "4"}, follow=True
        )
        self.assertContains(response, "You gave mihai 4 stars.")
        self.assertEqual(PlayerRating.objects.get().stars, 4)

    def test_organizer_marks_a_no_show_from_the_lineup(self):
        self.client.force_login(self.organizer)
        page = self.client.get(reverse("match_detail", args=[self.match.pk]))
        self.assertContains(page, "Didn't show up")

        response = self.client.post(
            reverse("participation_no_show", args=[self.mihai_spot.pk]), {"no_show": "1"}, follow=True
        )
        self.assertContains(response, "mihai is marked as a no-show.")
        self.assertContains(response, "Undo no-show")

    def test_players_cant_mark_no_shows(self):
        self.client.force_login(self.ana)
        response = self.client.post(reverse("participation_no_show", args=[self.mihai_spot.pk]), {"no_show": "1"})
        self.assertEqual(response.status_code, 403)

    def test_joined_matches_links_to_the_ratings(self):
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse("joined_matches")), "Rate your teammates")
