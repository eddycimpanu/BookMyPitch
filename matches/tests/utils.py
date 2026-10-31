from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from accounts.models import User
from matches.models import Match, Pitch


def make_user(username, role=User.Role.PLAYER):
    # No password: tests log in with force_login, and skipping hashing keeps them fast
    return User.objects.create_user(username=username, password=None, role=role)


def make_organizer(username="organizer"):
    return make_user(username, role=User.Role.ORGANIZER)


def make_pitch(created_by=None, name="Test Pitch", sector=Pitch.Sector.SECTOR_3):
    return Pitch.objects.create(
        name=name,
        address="Test Street 1",
        sector=sector,
        latitude=Decimal("44.430000"),
        longitude=Decimal("26.100000"),
        created_by=created_by,
    )


def make_match(organizer, pitch=None, starts_in=timedelta(days=2), spots_needed=3, **extra):
    return Match.objects.create(
        pitch=pitch or make_pitch(created_by=organizer),
        organizer=organizer,
        start_time=timezone.now() + starts_in,
        spots_needed=spots_needed,
        **extra,
    )