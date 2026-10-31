from django.test import TestCase
from django.urls import reverse

from matches.models import ChatMessage, Match, Participation
from matches.services import join_match

from .utils import make_match, make_organizer, make_user


class OrganizerAccessTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.player = make_user("player")
        self.match = make_match(self.organizer)
        self.organizer_urls = [reverse("my_matches"), reverse("match_create"), reverse("pitch_create")]

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(reverse("my_matches"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('my_matches')}")

    def test_player_gets_403_on_organizer_pages(self):
        self.client.force_login(self.player)
        for url in self.organizer_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_organizer_can_open_organizer_pages(self):
        self.client.force_login(self.organizer)
        for url in self.organizer_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_organizer_cannot_edit_another_organizers_match(self):
        self.client.force_login(make_organizer("other"))
        response = self.client.get(reverse("match_update", args=[self.match.pk]))
        self.assertEqual(response.status_code, 404)

    def test_organizer_cannot_cancel_another_organizers_match(self):
        self.client.force_login(make_organizer("other"))
        response = self.client.post(reverse("match_cancel", args=[self.match.pk]))
        self.assertEqual(response.status_code, 404)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.ACTIVE)

    def test_actions_that_change_data_reject_get_requests(self):
        self.client.force_login(self.organizer)
        for url in [reverse("match_cancel", args=[self.match.pk]), reverse("match_join", args=[self.match.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)


class JoinViewTests(TestCase):
    def test_player_joins_from_the_match_page(self):
        organizer = make_organizer()
        player = make_user("player")
        match = make_match(organizer)
        self.client.force_login(player)

        response = self.client.post(reverse("match_join", args=[match.pk]), follow=True)

        self.assertRedirects(response, reverse("match_detail", args=[match.pk]))
        self.assertContains(response, "See you on the pitch.")
        self.assertTrue(Participation.objects.filter(match=match, player=player).exists())


class ChatViewTests(TestCase):
    def setUp(self):
        self.organizer = make_organizer()
        self.player = make_user("player")
        self.match = make_match(self.organizer)
        join_match(self.match.pk, self.player)
        self.url = reverse("match_chat", args=[self.match.pk])

    def test_outsider_cannot_read_or_write(self):
        self.client.force_login(make_user("outsider"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"body": "Hi"}).status_code, 403)
        self.assertFalse(ChatMessage.objects.exists())

    def test_team_member_can_send_and_read_messages(self):
        self.client.force_login(self.player)
        response = self.client.post(self.url, {"body": "I'll bring the ball"})
        self.assertEqual(response.status_code, 201)

        messages = self.client.get(self.url).json()["messages"]
        self.assertEqual([message["body"] for message in messages], ["I'll bring the ball"])
        self.assertTrue(messages[0]["is_me"])

    def test_empty_message_is_rejected(self):
        self.client.force_login(self.player)
        self.assertEqual(self.client.post(self.url, {"body": "   "}).status_code, 400)

    def test_after_returns_only_newer_messages(self):
        first = ChatMessage.objects.create(match=self.match, author=self.organizer, body="first")
        ChatMessage.objects.create(match=self.match, author=self.organizer, body="second")
        self.client.force_login(self.player)

        messages = self.client.get(self.url, {"after": first.pk}).json()["messages"]

        self.assertEqual([message["body"] for message in messages], ["second"])