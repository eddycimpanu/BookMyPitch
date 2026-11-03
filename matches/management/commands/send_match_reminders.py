import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from matches.notifications import send_due_reminders


class Command(BaseCommand):
    help = "Email the organizer and players of every match that kicks off within the next 2 hours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--every",
            type=int,
            metavar="SECONDS",
            help="Keep running and check again every SECONDS seconds (stop with Ctrl+C).",
        )

    def handle(self, *args, every=None, **options):
        try:
            while True:
                count = send_due_reminders()
                self.stdout.write(f"{timezone.localtime():%H:%M:%S} Reminders sent for {count} match(es).")
                if not every:
                    return
                time.sleep(every)
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")
