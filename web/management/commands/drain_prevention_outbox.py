"""Scheduled retry entry point; no new service, port or automatic deployment."""
from django.core.management.base import BaseCommand
from web.prevention_store import Store


class Command(BaseCommand):
    help = 'Retry up to 100 durable prevention events using their original event IDs'

    def handle(self, *args, **options):
        self.stdout.write(str(Store().drain()))
