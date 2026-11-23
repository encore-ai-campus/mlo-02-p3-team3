"""Give each web test an isolated durable file, never the runtime purchase ledger."""
import tempfile
import unittest
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from django.test.runner import DiscoverRunner


class StateIsolation:
    def startTest(self, test):
        root=Path(settings.BASE_DIR)/'tmp'
        root.mkdir(exist_ok=True)
        self.state_directory=tempfile.TemporaryDirectory(dir=root)
        self.state_settings=override_settings(TICKETING_STATE_PATH=str(Path(self.state_directory.name)/'web.sqlite3'))
        self.state_settings.enable()
        super().startTest(test)

    def stopTest(self, test):
        try:
            super().stopTest(test)
        finally:
            self.state_settings.disable()
            self.state_directory.cleanup()


class IsolatedStateRunner(DiscoverRunner):
    def get_resultclass(self):
        base=super().get_resultclass() or unittest.TextTestResult
        return type('IsolatedStateResult',(StateIsolation,base),{})
