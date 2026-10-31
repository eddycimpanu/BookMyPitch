from django.test import TestCase
from django.urls import reverse

from .models import User


class SignUpTests(TestCase):
    def test_signup_creates_user_with_chosen_role_and_logs_them_in(self):
        response = self.client.post(reverse("signup"), {
            "username": "newcaptain",
            "email": "captain@example.com",
            "role": "organizer",
            "password1": "Str0ng-pass-123",
            "password2": "Str0ng-pass-123",
        })

        self.assertRedirects(response, reverse("home"))
        user = User.objects.get(username="newcaptain")
        self.assertEqual(user.role, User.Role.ORGANIZER)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)


class UserRoleTests(TestCase):
    def test_is_organizer_depends_on_role(self):
        organizer = User.objects.create_user(username="org", role=User.Role.ORGANIZER)
        player = User.objects.create_user(username="pla", role=User.Role.PLAYER)
        self.assertTrue(organizer.is_organizer)
        self.assertFalse(player.is_organizer)