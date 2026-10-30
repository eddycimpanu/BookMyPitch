from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ORGANIZER = "organizer", "Organizer"
        PLAYER = "player", "Player"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.PLAYER,
    )

    @property
    def is_organizer(self):
        return self.role == self.Role.ORGANIZER