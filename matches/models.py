from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Pitch(models.Model):
    """A synthetic football pitch, placed on the map by an organizer."""

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

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "pitches"

    def __str__(self):
        return f"{self.name} ({self.get_sector_display()})"


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


    @property
    def spot_states(self):
        """One boolean per spot (True = taken). Used to draw the spot dots in templates."""
        taken = min(self.spots_taken, self.spots_needed)
        return [True] * taken + [False] * (self.spots_needed - taken)

    def refresh_status(self):
        """Keep status in sync with the number of players. Cancelled matches stay cancelled."""
        if self.status == self.Status.CANCELLED:
            return
        self.status = self.Status.FULL if self.is_full else self.Status.ACTIVE
        
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
    def end_time(self):
        return self.start_time + timedelta(minutes=self.duration_minutes)

    @property
    def has_started(self):
        return self.start_time <= timezone.now()


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