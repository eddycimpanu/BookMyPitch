import threading

from django.db import connection
from django.test import TransactionTestCase

from matches.models import Match, Participation
from matches.services import MatchActionError, join_match

from .utils import make_match, make_organizer, make_user


class ConcurrentJoinTests(TransactionTestCase):
    """Many players click "Join" on the last spots at the same moment.

    Uses real threads, each with its own database connection, so PostgreSQL
    has to resolve the race. Without select_for_update, more players than
    spots could get in.
    """

    def test_parallel_joins_never_overbook_a_match(self):
        organizer = make_organizer()
        match = make_match(organizer, spots_needed=2)
        players = [make_user(f"player{i}") for i in range(6)]

        start_together = threading.Barrier(len(players))
        results = []
        results_lock = threading.Lock()

        def try_to_join(player):
            start_together.wait()  # all threads call join_match at the same moment
            try:
                join_match(match.pk, player)
                outcome = "joined"
            except MatchActionError:
                outcome = "rejected"
            except Exception as error:  # anything else shows up in the assertion below
                outcome = f"error: {error!r}"
            finally:
                connection.close()
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=try_to_join, args=(player,)) for player in players]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), ["joined"] * 2 + ["rejected"] * 4)
        self.assertEqual(Participation.objects.filter(match=match).count(), 2)
        match.refresh_from_db()
        self.assertEqual(match.status, Match.Status.FULL)