from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from matches.forms import MatchForm, WeeklyHoursForm
from matches.models import OpeningHours

from .utils import make_organizer, make_pitch, make_user

PITCH_DATA = {
    "name": "Arena Test",
    "address": "Strada Test 1",
    "sector": 3,
    "latitude": "44.430000",
    "longitude": "26.100000",
    "price_per_hour": "150",
}


def make_owner(username="owner"):
    return make_user(username, role=User.Role.OWNER)


def make_owned_pitch(owner, name="Owned Pitch"):
    pitch = make_pitch(created_by=owner, name=name)
    pitch.owner = owner
    pitch.price_per_hour = 150
    pitch.save()
    return pitch


def hours_data(open_days=range(7), opens=17 * 60, closes=23 * 60):
    data = {}
    for weekday in range(7):
        if weekday in open_days:
            data[f"day{weekday}_open"] = "on"
        data[f"day{weekday}_from"] = opens
        data[f"day{weekday}_to"] = closes
    return data


class OwnerPitchTests(TestCase):
    def setUp(self):
        self.owner = make_owner()

    def test_owner_lists_a_pitch_with_weekly_hours(self):
        self.client.force_login(self.owner)

        response = self.client.post(reverse("owner_pitch_create"), {**PITCH_DATA, **hours_data(open_days=range(5))})

        pitch = self.owner.owned_pitches.get(name="Arena Test")
        self.assertRedirects(response, reverse("pitch_detail", args=[pitch.pk]))
        self.assertEqual(sorted(pitch.opening_hours.values_list("weekday", flat=True)), [0, 1, 2, 3, 4])

    def test_only_owners_can_list_pitches(self):
        for user in [make_organizer(), make_user("player")]:
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("owner_pitch_create")).status_code, 403)

    def test_owner_cannot_edit_someone_elses_pitch(self):
        other_pitch = make_owned_pitch(make_owner("other_owner"))
        self.client.force_login(self.owner)
        response = self.client.get(reverse("owner_pitch_update", args=[other_pitch.pk]))
        self.assertEqual(response.status_code, 404)

    def test_closing_time_must_be_after_opening(self):
        form = WeeklyHoursForm(data=hours_data(opens=20 * 60, closes=18 * 60))
        self.assertFalse(form.is_valid())

    def test_pitch_must_be_open_at_least_one_day(self):
        form = WeeklyHoursForm(data=hours_data(open_days=[]))
        self.assertFalse(form.is_valid())

    def test_owned_pitches_cannot_be_picked_when_creating_a_match_directly(self):
        organizer_pitch = make_pitch(name="Organizer Pitch")
        owned_pitch = make_owned_pitch(self.owner)

        pitches = list(MatchForm().fields["pitch"].queryset)

        self.assertIn(organizer_pitch, pitches)
        self.assertNotIn(owned_pitch, pitches)

    def test_pitch_page_shows_opening_hours(self):
        pitch = make_owned_pitch(self.owner)
        OpeningHours.objects.create(pitch=pitch, weekday=0, opens_at=17 * 60, closes_at=23 * 60)

        response = self.client.get(reverse("pitch_detail", args=[pitch.pk]))

        self.assertContains(response, "17:00–23:00")
