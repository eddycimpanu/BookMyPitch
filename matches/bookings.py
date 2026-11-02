"""Booking rules for pitches listed by their owner: which times are free, and booking one."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Booking, Match, Pitch

# Bookings start on the hour or on the half hour
SLOT_STEP_MINUTES = 30

# How far ahead organizers can book, and how soon before kick-off
BOOKING_HORIZON_DAYS = 14
MIN_NOTICE = timedelta(hours=1)


class BookingError(Exception):
    """Raised when a pitch can't be booked. The message is shown to the user."""


def local_time(day, minutes):
    """The local date and time `minutes` after midnight on `day` (1440 is the next midnight)."""
    naive = datetime.combine(day, time()) + timedelta(minutes=minutes)
    return timezone.make_aware(naive)


def booking_days(pitch, today=None):
    """The days that can be booked from today on, each marked open or closed."""
    today = today or timezone.localdate()
    open_weekdays = {hours.weekday for hours in pitch.opening_hours.all()}
    days = [today + timedelta(days=offset) for offset in range(BOOKING_HORIZON_DAYS)]
    return [(day, day.weekday() in open_weekdays) for day in days]


def free_start_times(pitch, day, duration_minutes, now=None):
    """Every start time on `day` when the pitch is open and free for the whole duration."""
    hours = next((h for h in pitch.opening_hours.all() if h.weekday == day.weekday()), None)
    if hours is None:
        return []

    day_opens = local_time(day, hours.opens_at)
    day_closes = local_time(day, hours.closes_at)
    taken = list(
        pitch.bookings.filter(
            status=Booking.Status.CONFIRMED,
            start_time__lt=day_closes,
            end_time__gt=day_opens,
        ).values_list("start_time", "end_time")
    )
    earliest = (now or timezone.now()) + MIN_NOTICE

    starts = []
    last_start = hours.closes_at - duration_minutes
    for minutes in range(hours.opens_at, last_start + 1, SLOT_STEP_MINUTES):
        start = local_time(day, minutes)
        end = start + timedelta(minutes=duration_minutes)
        if start < earliest:
            continue
        # Two intervals overlap when each one starts before the other ends
        if any(booked_start < end and start < booked_end for booked_start, booked_end in taken):
            continue
        starts.append(start)
    return starts


def booking_price(pitch, duration_minutes):
    return (pitch.price_per_hour * Decimal(duration_minutes) / Decimal(60)).quantize(Decimal("0.01"))


def book_pitch(pitch_id, organizer, start_time, duration_minutes, spots_needed, price_per_player, description=""):
    """Book the pitch and create the match that goes with it, in one transaction."""
    with transaction.atomic():
        # Lock the pitch so organizers booking it at the same moment go one after another
        pitch = Pitch.objects.select_for_update().get(pk=pitch_id)
        if not pitch.is_bookable:
            raise BookingError("This pitch can't be booked through BookMyPitch.")

        day = timezone.localdate(start_time)
        if start_time not in free_start_times(pitch, day, duration_minutes):
            raise BookingError("That time isn't free any more. Pick another one.")

        match = Match.objects.create(
            pitch=pitch,
            organizer=organizer,
            start_time=start_time,
            duration_minutes=duration_minutes,
            spots_needed=spots_needed,
            price_per_player=price_per_player,
            description=description,
        )
        try:
            Booking.objects.create(
                pitch=pitch,
                match=match,
                start_time=start_time,
                end_time=start_time + timedelta(minutes=duration_minutes),
                price=booking_price(pitch, duration_minutes),
            )
        except IntegrityError:
            # Backup: the exclusion constraint in PostgreSQL caught an overlapping booking.
            # Raising here rolls back the whole transaction, so the match is not kept either.
            raise BookingError("That time was just booked by someone else. Pick another one.")

    return match


def release_booking(match):
    """Cancel the booking of a cancelled match, so its time is free again. Returns True if there was one."""
    return Booking.objects.filter(match=match, status=Booking.Status.CONFIRMED).update(
        status=Booking.Status.CANCELLED
    ) > 0
