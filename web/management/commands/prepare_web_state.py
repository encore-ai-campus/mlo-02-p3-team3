from django.core.management.base import BaseCommand, CommandError
from web.prevention_store import Store

class Command(BaseCommand):
    help = 'Prepare web indexes explicitly; never drops data or existing indexes'
    def add_arguments(self, parser): parser.add_argument('--check', action='store_true')
    def handle(self, *args, **options):
        store=Store()
        if not options['check']:store.prepare()
        missing=store.check_ready()
        if missing:raise CommandError('Missing/incompatible indexes: '+ '; '.join(missing))
        self.stdout.write('Web state indexes ready')
