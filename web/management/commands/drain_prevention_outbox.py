"""Scheduled retry entry point; no new service, port or automatic deployment."""
from django.core.management.base import BaseCommand
from web.prevention_store import Store
from web.outbox import Worker
import signal
import threading


class Command(BaseCommand):
    help = 'Retry up to 100 durable prevention events using their original event IDs'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--interval', type=float, default=1)

    def handle(self, *args, **options):
        stop=threading.Event()
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
        worker=Worker(Store(),stop=stop)
        while not stop.is_set():
            self.stdout.write(str(worker.drain(max(1,options['limit']))))
            if not options['watch']:break
            stop.wait(max(.1,options['interval']))
