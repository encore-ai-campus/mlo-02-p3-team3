"""Initialize only the isolated test replica set on loopback port 27029."""
import time
from pymongo import MongoClient
from pymongo.errors import OperationFailure
client=MongoClient('mongodb://127.0.0.1:27029/?directConnection=true',serverSelectionTimeoutMS=5000)
try:
    client.admin.command('replSetInitiate',{'_id':'conversion-test','members':[{'_id':0,'host':'localhost:27029'}]})
except OperationFailure as error:
    if error.code != 23: raise
for _ in range(60):
    if client.admin.command('hello').get('isWritablePrimary'): break
    time.sleep(.5)
else: raise RuntimeError('Test primary not ready')
client.close()
print('Isolated test MongoDB ready')
