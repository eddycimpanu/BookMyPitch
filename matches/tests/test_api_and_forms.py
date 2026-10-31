from datetime import date, timedelta

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from matches.forms import MatchForm, PitchForm
from matches.models import Match
from matches.services import join_match
from matches.views import week_ranges

from .utils import make_match, make_organizer, make_pitch, make_user


class PitchesApiTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()

    def get_api(self):
        return self.client.get(reverse("pitches_api")).json()

    def test_lists_only_upcoming_non_cancelled_matches(self):
        pitch = make_pitch(self.organizer)
        upcoming = make_match(self.organizer, pitch=pitch)
        make_match(self.organizer, pitch=pitch, starts_in=-timedelta(hours=1))
        make_match(self.organizer, pitch=pitch, status=Match.Status.CANCELLED)

        match_ids = [match["id"] for pitch in self.get_api()["pitches"] for match in pitch["matches"]]

        self.assertEqual(match_ids, [upcoming.pk])

    def test_reports_taken_and_open_spots(self):
        match = make_match(self.organizer, spots_needed=3)
        join_match(match.pk, make_user("player"))

        match_data = self.get_api()["pitches"][0]["matches"][0]

        self.assertEqual(match_data["spots_taken"], 1)
        self.assertEqual(match_data["spots_left"], 2)

    def test_query_count_does_not_grow_with_the_number_of_pitches(self):
        make_match(self.organizer)
        with CaptureQueriesContext(connection) as one_pitch:
            self.client.get(reverse("pitches_api"))

        for _ in range(5):
            make_match(self.organizer)  # each match gets its own new pitch
        with CaptureQueriesContext(connection) as six_pitches:
            self.client.get(reverse("pitches_api"))

        self.assertEqual(len(one_pitch), len(six_pitches))


class WeekRangesTests(SimpleTestCase):
    def test_on_saturday_this_week_is_the_weekend(self):
        this_week, next_week = week_ranges(date(2026, 10, 31))
        self.assertEqual(this_week, (date(2026, 10, 31), date(2026, 11, 1)))
        self.assertEqual(next_week, (date(2026, 11, 2), date(2026, 11, 8)))

    def test_on_monday_this_week_is_the_whole_week(self):
        this_week, next_week = week_ranges(date(2026, 11, 2))
        self.assertEqual(this_week, (date(2026, 11, 2), date(2026, 11, 8)))
        self.assertEqual(next_week, (date(2026, 11, 9), date(2026, 11, 15)))

    def test_on_sunday_this_week_is_only_today(self):
        this_week, next_week = week_ranges(date(2026, 11, 1))
        self.assertEqual(this_week, (date(2026, 11, 1), date(2026, 11, 1)))
        self.assertEqual(next_week, (date(2026, 11, 2), date(2026, 11, 8)))


class MatchFormTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.pitch = make_pitch(self.organizer)

    def form_data(self, **overrides):
        kickoff = timezone.localtime() + timedelta(days=1)
        data = {
            "pitch": self.pitch.pk,
            "start_time": kickoff.strftime("%Y-%m-%dT%H:%M"),
            "duration_minutes": 60,
            "spots_needed": 4,
            "price_per_player": "25.00",
            "description": "",
        }
        data.update(overrides)
        return data

    def test_valid_match(self):
        form = MatchForm(data=self.form_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_kickoff_must_be_in_the_future(self):
        past = (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        form = MatchForm(data=self.form_data(start_time=past))
        self.assertFalse(form.is_valid())
        self.assertIn("start_time", form.errors)

    def test_cannot_reduce_spots_below_players_who_already_joined(self):
        match = make_match(self.organizer, pitch=self.pitch, spots_needed=3)
        join_match(match.pk, make_user("first"))
        join_match(match.pk, make_user("second"))

        form = MatchForm(data=self.form_data(spots_needed=1), instance=match)

        self.assertFalse(form.is_valid())
        self.assertIn("spots_needed", form.errors)


class PitchFormTests(TestCase):
    def form_data(self, **overrides):
        data = {
            "name": "Arena Test",
            "address": "Strada Test 1",
            "sector": 3,
            "latitude": "44.430000",
            "longitude": "26.100000",
        }
        data.update(overrides)
        return data

    def test_valid_pitch_in_bucharest(self):
        form = PitchForm(data=self.form_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_location_outside_bucharest_is_rejected(self):
        form = PitchForm(data=self.form_data(latitude="45.650000"))  # Brasov
        self.assertFalse(form.is_valid())
        self.assertIn("latitude", form.errors)

    def test_location_is_required(self):
        form = PitchForm(data=self.form_data(latitude="", longitude=""))
        self.assertFalse(form.is_valid())
        self.assertIn("latitude", form.errors)