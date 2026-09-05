"""Exercise a local Echo instance without logging credentials or contacting production."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8010/v1')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    if urlparse(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('This smoke script only accepts loopback hosts.')
    opener = build_opener(ProxyHandler({}))
    token = ''

    def call(path: str, payload: dict | None = None) -> dict:
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with opener.open(req, timeout=30) as response:
            return json.load(response)

    call('/health')
    email = 'smoke-' + uuid.uuid4().hex + '@example.com'
    auth = call('/auth/register', {'email': email, 'password': uuid.uuid4().hex})
    token = auth['token']
    session = call('/sessions/ensure', {'user_id': email})
    sid = session['id']
    response = call(f'/sessions/{sid}/messages', {'content': 'Hello from the local Echo smoke test.'})
    assert response['session_id'] == sid and response['message']
    request = {'content': 'Please reply to this local test question.', 'client_message_id': uuid.uuid4().hex}
    accepted = call(f'/sessions/{sid}/messages/async', request)
    assert accepted['session_id'] == sid
    call(f'/sessions/{sid}/messages/async', request)  # same ID must be idempotent
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        messages = call(f'/sessions/{sid}')['messages']
        if sum(m.get('role') == 'assistant' for m in messages) >= 2:
            break
        time.sleep(0.3)
    else:
        raise AssertionError('Async reply did not complete within 25 seconds')
    matches = [m for m in messages if m.get('role') == 'user' and m.get('content') == request['content']]
    assert len(matches) == 1, 'Duplicate async message was persisted'
    print('PASS: health, registration, session, synchronous reply, async reply and idempotent retry')
    print('Synthetic account remains in local test data. No credentials were printed.')


if __name__ == '__main__':
    main()
