import os
import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

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


class ProfileTests(TestCase):
    def setUp(self):
        # Uploaded photos go to a temporary folder, deleted after each test
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username="ana", email="ana@example.com")
        self.client.force_login(self.user)

    def image_upload(self, size=(600, 400), name="me.png"):
        buffer = BytesIO()
        Image.new("RGB", size, "green").save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_player_sets_position_level_and_photo(self):
        response = self.client.post(reverse("profile_edit"), {
            "position": "midfielder",
            "level": "intermediate",
            "photo": self.image_upload(),
        })

        self.assertRedirects(response, reverse("profile", args=["ana"]))
        self.user.refresh_from_db()
        self.assertEqual(self.user.position, User.Position.MIDFIELDER)
        self.assertEqual(self.user.level, User.Level.INTERMEDIATE)
        with Image.open(self.user.photo.path) as photo:
            self.assertEqual(photo.size, (256, 256))  # cropped to a square and shrunk
            self.assertEqual(photo.format, "JPEG")

    def test_files_that_are_not_images_are_refused(self):
        fake = SimpleUploadedFile("me.png", b"not really an image", content_type="image/png")
        response = self.client.post(reverse("profile_edit"), {"photo": fake})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.get(pk=self.user.pk).photo)

    def test_removing_the_photo_deletes_the_file(self):
        self.client.post(reverse("profile_edit"), {"photo": self.image_upload()})
        self.user.refresh_from_db()
        path = self.user.photo.path

        self.client.post(reverse("profile_edit"), {"remove_photo": "on"})

        self.user.refresh_from_db()
        self.assertFalse(self.user.photo)
        self.assertFalse(os.path.exists(path))

    def test_profile_page_shows_the_player(self):
        self.user.position = User.Position.GOALKEEPER
        self.user.save()
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Goalkeeper")
        self.assertContains(response, "Reliability")
        self.assertContains(response, "Edit profile")

        other = User.objects.create_user(username="mihai")
        self.assertNotContains(self.client.get(reverse("profile", args=[other.username])), "Edit profile")

    def test_pitch_owners_only_set_a_photo(self):
        owner = User.objects.create_user(username="becali", role=User.Role.OWNER)
        self.client.force_login(owner)
        form = self.client.get(reverse("profile_edit")).context["form"]
        self.assertEqual(list(form.fields), ["photo"])

    def test_unknown_player_is_a_404(self):
        self.assertEqual(self.client.get(reverse("profile", args=["nobody"])).status_code, 404)
