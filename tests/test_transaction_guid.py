import re
import uuid

from app import app

app.config['TESTING'] = True

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def test_transaction_guid_returns_uuid4():
    client = app.test_client()
    response = client.post('/transaction_guid')

    assert response.status_code == 200
    data = response.get_json()
    assert 'transaction_guid' in data
    assert UUID_RE.match(data['transaction_guid'])
    uuid.UUID(data['transaction_guid'], version=4)


def test_transaction_guid_is_unique_per_request():
    client = app.test_client()
    first = client.post('/transaction_guid').get_json()['transaction_guid']
    second = client.post('/transaction_guid').get_json()['transaction_guid']
    assert first != second
