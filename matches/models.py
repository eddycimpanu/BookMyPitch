from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def minutes_label(minutes):
    """540 -> '09:00', 1440 -> '24:00'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# Every half hour from 00:00 to 24:00, stored as minutes after midnight
HALF_HOURS = [(minutes, minutes_label(minutes)) for minutes in range(0, 24 * 60 + 1, 30)]


# ---------- Pitches ----------


class Pitch(models.Model):
    """A synthetic football pitch on the map.

    Added either by an organizer (just a place to play) or by its owner
    (bookable by the hour, with a price and weekly opening hours).
    """

    class Sector(models.IntegerChoices):
        SECTOR_1 = 1, "Sector 1"
        SECTOR_2 = 2, "Sector 2"
        SECTOR_3 = 3, "Sector 3"
        SECTOR_4 = 4, "Sector 4"
        SECTOR_5 = 5, "Sector 5"
        SECTOR_6 = 6, "Sector 6"
        ILFOV = 7, "Ilfov (outside the city)"

    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255)
    sector = models.PositiveSmallIntegerField(choices=Sector.choices)

    # Bounds cover Bucharest and the surrounding Ilfov area
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("44.2")), MaxValueValidator(Decimal("44.7"))],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("25.8")), MaxValueValidator(Decimal("26.4"))],
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="pitches",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Set only for pitches listed by their owner. Those can be booked by the hour.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="owned_pitches",
    )
    price_per_hour = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="In RON.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "pitches"

    def __str__(self):
        return f"{self.name} ({self.get_sector_display()})"

    @property
    def is_bookable(self):
        return self.owner_id is not None


class OpeningHours(models.Model):
    """When a bookable pitch is open on one day of the week."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    pitch = models.ForeignKey(Pitch, on_delete=models.CASCADE, related_name="opening_hours")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    opens_at = models.PositiveSmallIntegerField(
        choices=HALF_HOURS,
        help_text="Minutes after midnight.",
    )
    closes_at = models.PositiveSmallIntegerField(
        choices=HALF_HOURS,
        help_text="Minutes after midnight. 1440 means midnight.",
    )

    class Meta:
        ordering = ["weekday"]
        verbose_name_plural = "opening hours"
        constraints = [
            models.UniqueConstraint(
                fields=["pitch", "weekday"],
                name="one_opening_hours_per_day",
            ),
            models.CheckConstraint(
                condition=models.Q(closes_at__gt=models.F("opens_at")),
                name="opening_hours_close_after_open",
            ),
        ]

    def __str__(self):
        return f"{self.pitch.name}: {self.get_weekday_display()} {self.opens_label}–{self.closes_label}"

    @property
    def opens_label(self):
        return minutes_label(self.opens_at)

    @property
    def closes_label(self):
        return minutes_label(self.closes_at)


# ---------- Matches ----------


class Match(models.Model):
    """A game on a pitch, created by an organizer, looking for players."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FULL = "full", "Full"
        CANCELLED = "cancelled", "Cancelled"

    class Duration(models.IntegerChoices):
        ONE_HOUR = 60, "1 hour"
        NINETY_MINUTES = 90, "1.5 hours"
        TWO_HOURS = 120, "2 hours"
        THREE_HOURS = 180, "3 hours"

    pitch = models.ForeignKey(Pitch, on_delete=models.CASCADE, related_name="matches")
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organized_matches",
    )
    players = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="Participation",
        related_name="joined_matches",
        blank=True,
    )

    start_time = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(
        choices=Duration.choices,
        default=Duration.ONE_HOUR,
    )
    spots_needed = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="How many more players the organizer is looking for.",
    )
    price_per_player = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="In RON. Use 0 for free matches.",
    )
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time"]
        verbose_name_plural = "matches"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(spots_needed__gte=1),
                name="match_spots_needed_at_least_one",
            ),
            models.CheckConstraint(
                condition=models.Q(price_per_player__gte=0),
                name="match_price_not_negative",
            ),
        ]

    def __str__(self):
        local_start = timezone.localtime(self.start_time)
        return f"{self.pitch.name} – {local_start:%d %b %Y, %H:%M}"

    # Spots

    @property
    def spots_taken(self):
        return self.participations.count()

    @property
    def spots_left(self):
        return max(self.spots_needed - self.spots_taken, 0)

    @property
    def is_full(self):
        return self.spots_left == 0

    @property
    def spot_states(self):
        """One boolean per spot (True = taken). Used to draw the spot dots in templates."""
        taken = min(self.spots_taken, self.spots_needed)
        return [True] * taken + [False] * (self.spots_needed - taken)

    # Time

    @property
    def end_time(self):
        return self.start_time + timedelta(minutes=self.duration_minutes)

    @property
    def has_started(self):
        return self.start_time <= timezone.now()

    # Status

    def refresh_status(self):
        """Keep status in sync with the number of players. Cancelled matches stay cancelled."""
        if self.status == self.Status.CANCELLED:
            return
        self.status = self.Status.FULL if self.is_full else self.Status.ACTIVE


class Participation(models.Model):
    """A player who took a spot in a match."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="participations")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="participations",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["match", "player"],
                name="unique_player_per_match",
            ),
        ]

    def __str__(self):
        return f"{self.player} → {self.match}"


class MatchExit(models.Model):
    """Records when a player left a match or was removed by the organizer.

    Used to enforce the 1-hour rejoin cooldown and to keep removed players out.
    """

    class Reason(models.TextChoices):
        LEFT = "left", "Left the match"
        REMOVED = "removed", "Removed by the organizer"

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="exits")
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="match_exits",
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["match", "player"])]

    def __str__(self):
        return f"{self.player} {self.get_reason_display().lower()} ({self.match})"


# ---------- Pitch bookings ----------


class TsTzRange(models.Func):
    """PostgreSQL's tstzrange(start, end): a time interval, used to detect overlapping bookings."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Booking(models.Model):
    """An organizer renting a bookable pitch for a time slot. Every booking comes with its match."""

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    pitch = models.ForeignKey(Pitch, on_delete=models.CASCADE, related_name="bookings")
    match = models.OneToOneField(Match, on_delete=models.CASCADE, related_name="booking")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        help_text="What the organizer pays for the pitch, in RON, at the price when it was booked.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    created_at = models.DateTimeField(auto_now_add=True)

    # Who cancelled the booking (the organizer or the pitch owner), and the owner's reason
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    cancel_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="booking_ends_after_it_starts",
            ),
            # PostgreSQL itself refuses two confirmed bookings of the same pitch whose times overlap.
            # "[)" means the start is included and the end is not, so 20:00-21:00 and 21:00-22:00 can coexist.
            ExclusionConstraint(
                name="no_overlapping_bookings",
                expressions=[
                    (TsTzRange("start_time", "end_time", RangeBoundary()), RangeOperators.OVERLAPS),
                    ("pitch", RangeOperators.EQUAL),
                ],
                condition=models.Q(status="confirmed"),
            ),
        ]

    def __str__(self):
        local_start = timezone.localtime(self.start_time)
        local_end = timezone.localtime(self.end_time)
        return f"{self.pitch.name}: {local_start:%d %b %Y, %H:%M}–{local_end:%H:%M}"

    @property
    def cancelled_by_owner(self):
        return self.cancelled_by_id is not None and self.cancelled_by_id == self.pitch.owner_id


class BlockedTime(models.Model):
    """A time when the owner closes a bookable pitch, for example for maintenance or a private event."""

    pitch = models.ForeignKey(Pitch, on_delete=models.CASCADE, related_name="blocked_times")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    reason = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="blocked_time_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        local_start = timezone.localtime(self.start_time)
        local_end = timezone.localtime(self.end_time)
        return f"{self.pitch.name} blocked {local_start:%d %b %Y, %H:%M}–{local_end:%H:%M}"


# ---------- Team chat ----------


class ChatMessage(models.Model):
    """A message in a match's team chat."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    body = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author}: {self.body[:40]}"
