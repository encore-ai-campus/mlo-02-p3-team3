"""Wait for the isolated MongoDB server, then initialize its replica set."""
import time

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure


def wait_until(operation, description, timeout=60):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if operation():
                return
        except ConnectionFailure as error:
            last_error = type(error).__name__
        time.sleep(.5)
    suffix = f' ({last_error})' if last_error else ''
    raise RuntimeError(f'Test MongoDB {description} timed out after {timeout}s{suffix}; inspect the mongo-test container logs')


def initialize(timeout=60):
    with MongoClient('mongodb://127.0.0.1:27029/?directConnection=true',
                     serverSelectionTimeoutMS=1000, connectTimeoutMS=1000,
                     socketTimeoutMS=2000) as client:
        # Compose starting a process does not mean its TCP listener is ready.
        wait_until(lambda: client.admin.command('ping').get('ok') == 1,
                   'listener readiness', timeout)

        def initiate():
            try:
                result = client.admin.command('replSetInitiate', {
                    '_id': 'conversion-test',
                    'members': [{'_id': 0, 'host': 'localhost:27029'}],
                })
                return result.get('ok') == 1
            except OperationFailure as error:
                if error.code == 23:  # AlreadyInitialized, including a lost reply.
                    return True
                if error.code in (91, 10107, 11600, 11602, 13435):
                    return False  # Server restart/election in progress.
                raise

        wait_until(initiate, 'replica set initialization', timeout)
        wait_until(lambda: client.admin.command('hello').get('isWritablePrimary', False),
                   'primary election', timeout)
    print('Isolated test MongoDB ready')


if __name__ == '__main__':
    initialize()
