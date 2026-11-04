from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ORGANIZER = "organizer", "Organizer"
        PLAYER = "player", "Player"
        OWNER = "owner", "Pitch owner"

    class Position(models.TextChoices):
        GOALKEEPER = "goalkeeper", "Goalkeeper"
        DEFENDER = "defender", "Defender"
        MIDFIELDER = "midfielder", "Midfielder"
        FORWARD = "forward", "Forward"
        ANYWHERE = "anywhere", "Anywhere"

    class Level(models.TextChoices):
        BEGINNER = "beginner", "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.PLAYER,
    )

    # Profile. All optional: players fill them in when they want.
    position = models.CharField(max_length=20, choices=Position.choices, blank=True)
    level = models.CharField(max_length=20, choices=Level.choices, blank=True)
    photo = models.ImageField(upload_to="avatars/", blank=True)

    @property
    def is_organizer(self):
        return self.role == self.Role.ORGANIZER

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER
