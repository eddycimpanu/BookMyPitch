import threading
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from matches.bookings import BookingError, book_pitch, free_start_times, local_time, release_booking
from matches.models import Booking, Match, OpeningHours

from .utils import make_organizer, make_owned_pitch, make_owner, make_pitch, make_user


def open_every_day(pitch, opens=17 * 60, closes=23 * 60):
    OpeningHours.objects.bulk_create(
        OpeningHours(pitch=pitch, weekday=weekday, opens_at=opens, closes_at=closes) for weekday in range(7)
    )


class BookingSetupMixin:
    """A pitch open every day 17:00-23:00 at 150 RON per hour, and a day three days from now."""

    def setUp(self):
        self.owner = make_owner()
        self.organizer = make_organizer()
        self.pitch = make_owned_pitch(self.owner)
        open_every_day(self.pitch)
        self.day = timezone.localdate() + timedelta(days=3)

    def at(self, hhmm):
        hours, minutes = map(int, hhmm.split(":"))
        return local_time(self.day, hours * 60 + minutes)

    def book(self, hhmm, duration=60, organizer=None):
        return book_pitch(
            self.pitch.pk,
            organizer or self.organizer,
            self.at(hhmm),
            duration,
            spots_needed=4,
            price_per_player=Decimal("20"),
        )


class FreeTimesTests(BookingSetupMixin, TestCase):
    def test_free_times_follow_the_opening_hours(self):
        starts = free_start_times(self.pitch, self.day, 60)
        self.assertEqual(starts[0], self.at("17:00"))
        self.assertEqual(starts[-1], self.at("22:00"))  # the last hour that ends by closing time
        self.assertEqual(len(starts), 11)  # every half hour from 17:00 to 22:00

    def test_closed_day_has_no_free_times(self):
        self.pitch.opening_hours.filter(weekday=self.day.weekday()).delete()
        self.assertEqual(free_start_times(self.pitch, self.day, 60), [])

    def test_booked_hour_and_overlapping_starts_are_not_free(self):
        self.book("20:00")
        starts = free_start_times(self.pitch, self.day, 60)
        for taken in ["19:30", "20:00", "20:30"]:
            self.assertNotIn(self.at(taken), starts)
        for free in ["19:00", "21:00"]:
            self.assertIn(self.at(free), starts)

    def test_times_less_than_an_hour_away_are_not_offered(self):
        starts = free_start_times(self.pitch, self.day, 60, now=self.at("19:30"))
        self.assertEqual(starts[0], self.at("20:30"))


class BookPitchTests(BookingSetupMixin, TestCase):
    def test_booking_creates_the_match_and_the_booking(self):
        match = self.book("20:00", duration=90)

        self.assertEqual(match.pitch, self.pitch)
        self.assertEqual(match.duration_minutes, 90)
        self.assertEqual(match.booking.end_time, self.at("21:30"))
        self.assertEqual(match.booking.price, Decimal("225.00"))  # 1.5 hours at 150 RON

    def test_overlapping_time_cannot_be_booked(self):
        self.book("20:00")
        with self.assertRaises(BookingError):
            self.book("20:30", organizer=make_organizer("other"))
        self.assertEqual(Match.objects.count(), 1)

    def test_back_to_back_bookings_are_allowed(self):
        self.book("20:00")
        self.book("21:00", organizer=make_organizer("other"))
        self.assertEqual(Booking.objects.count(), 2)

    def test_cancelled_booking_frees_the_time(self):
        match = self.book("20:00")
        release_booking(match)

        self.assertIn(self.at("20:00"), free_start_times(self.pitch, self.day, 60))
        self.book("20:00", organizer=make_organizer("other"))

    def test_pitch_without_owner_cannot_be_booked(self):
        organizer_pitch = make_pitch(name="Organizer Pitch")
        with self.assertRaises(BookingError):
            book_pitch(organizer_pitch.pk, self.organizer, self.at("20:00"), 60, 4, Decimal("0"))

    def test_database_refuses_overlapping_bookings(self):
        self.book("20:00")
        other_match = Match.objects.create(
            pitch=self.pitch, organizer=self.organizer, start_time=self.at("20:30"), spots_needed=4
        )
        # Skips the checks in book_pitch on purpose: PostgreSQL's exclusion constraint must still refuse it
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.create(
                pitch=self.pitch,
                match=other_match,
                start_time=self.at("20:30"),
                end_time=self.at("21:30"),
                price=Decimal("150"),
            )


class BookingViewTests(BookingSetupMixin, TestCase):
    def booking_url(self):
        return f"{reverse('pitch_book', args=[self.pitch.pk])}?date={self.day.isoformat()}&duration=60"

    def test_organizer_books_through_the_booking_page(self):
        self.client.force_login(self.organizer)

        response = self.client.post(self.booking_url(), {
            "start": self.at("20:00").isoformat(),
            "spots_needed": 5,
            "price_per_player": "25",
            "description": "",
        })

        booking = Booking.objects.get(pitch=self.pitch)
        self.assertRedirects(response, reverse("match_detail", args=[booking.match.pk]))
        self.assertEqual(booking.start_time, self.at("20:00"))

    def test_only_organizers_can_book(self):
        for user in [make_user("player"), self.owner]:
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.booking_url()).status_code, 403)

    def test_cancelling_the_match_frees_the_booked_time(self):
        match = self.book("20:00")
        self.client.force_login(self.organizer)

        self.client.post(reverse("match_cancel", args=[match.pk]))

        match.booking.refresh_from_db()
        self.assertEqual(match.booking.status, Booking.Status.CANCELLED)
        self.assertIn(self.at("20:00"), free_start_times(self.pitch, self.day, 60))

    def test_booked_match_keeps_its_pitch_and_time_when_edited(self):
        match = self.book("20:00")
        self.client.force_login(self.organizer)

        response = self.client.get(reverse("match_update", args=[match.pk]))

        fields = set(response.context["form"].fields)
        self.assertEqual(fields, {"spots_needed", "price_per_player", "description"})

    def test_map_shows_bookable_pitches_without_matches(self):
        pitches = self.client.get(reverse("pitches_api")).json()["pitches"]

        self.assertEqual(len(pitches), 1)
        self.assertTrue(pitches[0]["bookable"])
        self.assertEqual(pitches[0]["matches"], [])


class ConcurrentBookingTests(TransactionTestCase):
    """Several organizers try to book the same hour at the same moment: exactly one gets it."""

    def test_parallel_bookings_never_overlap(self):
        pitch = make_owned_pitch(make_owner())
        open_every_day(pitch)
        start = local_time(timezone.localdate() + timedelta(days=3), 20 * 60)
        organizers = [make_organizer(f"organizer{i}") for i in range(4)]

        start_together = threading.Barrier(len(organizers))
        results = []
        results_lock = threading.Lock()

        def try_to_book(organizer):
            start_together.wait()
            try:
                book_pitch(pitch.pk, organizer, start, 60, 4, Decimal("0"))
                outcome = "booked"
            except BookingError:
                outcome = "refused"
            except Exception as error:  # anything else shows up in the assertion below
                outcome = f"error: {error!r}"
            finally:
                connection.close()
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=try_to_book, args=(organizer,)) for organizer in organizers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), ["booked"] + ["refused"] * 3)
        self.assertEqual(Booking.objects.filter(pitch=pitch).count(), 1)
