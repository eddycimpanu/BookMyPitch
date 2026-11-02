from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from matches.bookings import BookingError, block_time, book_pitch, cancel_booking, free_start_times
from matches.forms import BlockTimeForm
from matches.models import BlockedTime, Booking, Match

from .test_bookings import BookingSetupMixin, open_every_day
from .utils import make_owned_pitch, make_owner, make_user


class OwnerBookingsPageTests(BookingSetupMixin, TestCase):
    def book_on(self, pitch, hhmm):
        return book_pitch(pitch.pk, self.organizer, self.at(hhmm), 60, 4, Decimal("0"))

    def test_owner_sees_bookings_of_their_own_pitches_only(self):
        own = self.book("20:00")
        other_pitch = make_owned_pitch(make_owner("other_owner"), name="Other Pitch")
        open_every_day(other_pitch)
        other_match = self.book_on(other_pitch, "20:00")

        self.client.force_login(self.owner)
        response = self.client.get(reverse("owner_bookings"))

        listed = [booking for _, bookings in response.context["days"] for booking in bookings]
        self.assertEqual(listed, [own.booking])
        self.assertNotIn(other_match.booking, listed)

    def test_only_owners_can_open_the_bookings_page(self):
        for user in [self.organizer, make_user("player")]:
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("owner_bookings")).status_code, 403)


class OwnerCancelBookingTests(BookingSetupMixin, TestCase):
    def test_owner_cancels_a_booking_with_a_reason(self):
        match = self.book("20:00")
        self.client.force_login(self.owner)

        self.client.post(reverse("owner_booking_cancel", args=[match.booking.pk]), {"reason": "Pitch repairs"})

        match.refresh_from_db()
        match.booking.refresh_from_db()
        self.assertEqual(match.status, Match.Status.CANCELLED)
        self.assertEqual(match.booking.status, Booking.Status.CANCELLED)
        self.assertTrue(match.booking.cancelled_by_owner)
        self.assertIn(self.at("20:00"), free_start_times(self.pitch, self.day, 60))

        response = self.client.get(reverse("match_detail", args=[match.pk]))
        self.assertContains(response, "Reason: Pitch repairs")

    def test_another_owner_cannot_cancel_the_booking(self):
        match = self.book("20:00")
        self.client.force_login(make_owner("other_owner"))

        response = self.client.post(reverse("owner_booking_cancel", args=[match.booking.pk]))

        self.assertEqual(response.status_code, 404)
        match.booking.refresh_from_db()
        self.assertEqual(match.booking.status, Booking.Status.CONFIRMED)

    def test_a_booking_that_already_started_cannot_be_cancelled(self):
        start = timezone.now() - timedelta(minutes=10)
        match = Match.objects.create(pitch=self.pitch, organizer=self.organizer, start_time=start, spots_needed=4)
        booking = Booking.objects.create(
            pitch=self.pitch, match=match, start_time=start, end_time=start + timedelta(hours=1), price=Decimal("150")
        )
        with self.assertRaises(BookingError):
            cancel_booking(booking.pk, self.owner)

    def test_organizer_cancelling_the_match_is_not_shown_as_the_owner(self):
        match = self.book("20:00")
        self.client.force_login(self.organizer)

        self.client.post(reverse("match_cancel", args=[match.pk]))

        match.booking.refresh_from_db()
        self.assertEqual(match.booking.cancelled_by, self.organizer)
        self.assertFalse(match.booking.cancelled_by_owner)


class BlockedTimeTests(BookingSetupMixin, TestCase):
    def test_blocked_time_is_not_free_and_cannot_be_booked(self):
        block_time(self.pitch.pk, self.owner, self.at("19:00"), self.at("21:00"))

        starts = free_start_times(self.pitch, self.day, 60)
        for blocked in ["18:30", "19:00", "20:00"]:
            self.assertNotIn(self.at(blocked), starts)
        self.assertIn(self.at("21:00"), starts)

        with self.assertRaises(BookingError):
            self.book("20:00")

    def test_owner_cannot_block_a_time_that_is_already_booked(self):
        self.book("20:00")
        with self.assertRaises(BookingError):
            block_time(self.pitch.pk, self.owner, self.at("19:00"), self.at("21:00"))
        self.assertFalse(BlockedTime.objects.exists())

    def test_owner_blocks_and_opens_a_time_from_the_bookings_page(self):
        self.client.force_login(self.owner)

        self.client.post(reverse("owner_bookings"), {
            "pitch": self.pitch.pk,
            "day": self.day.isoformat(),
            "starts_at": 19 * 60,
            "ends_at": 21 * 60,
            "reason": "Private event",
        })
        blocked = BlockedTime.objects.get(pitch=self.pitch)
        self.assertNotIn(self.at("19:00"), free_start_times(self.pitch, self.day, 60))

        self.client.post(reverse("owner_unblock", args=[blocked.pk]))
        self.assertIn(self.at("19:00"), free_start_times(self.pitch, self.day, 60))

    def test_block_must_end_after_it_starts(self):
        form = BlockTimeForm(
            data={"pitch": self.pitch.pk, "day": self.day.isoformat(), "starts_at": 21 * 60, "ends_at": 19 * 60},
            owner=self.owner,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("ends_at", form.errors)
