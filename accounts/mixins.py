from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class OrganizerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Allows access only to logged-in users with the organizer role.

    Anonymous users are redirected to the login page.
    Logged-in players get a 403 Forbidden response.
    """

    def test_func(self):
        return self.request.user.is_organizer