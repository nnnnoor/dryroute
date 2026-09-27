"""OAuth and isolation tests use mocked Google responses; no credentials or network."""
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from app.integrations import google_calendar as google


@pytest.fixture
def browser(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, 'google_client_id', 'test-client')
    monkeypatch.setattr(client.app.state.settings, 'google_client_secret', 'test-secret')
    browser = TestClient(client.app)
    yield browser
    browser.close()


def begin(browser):
    response = browser.get('/auth/google/start', follow_redirects=False)
    assert response.status_code == 303
    params = parse_qs(urlparse(response.headers['location']).query)
    assert params['scope'] == [google.SCOPE]
    assert params['code_challenge_method'] == ['S256']
    assert 'httponly' in response.headers['set-cookie'].lower()
    return params['state'][0]


def response(data, status=200):
    return httpx.Response(status, json=data, request=httpx.Request('POST', 'https://oauth2.googleapis.com/token'))


def authorize(browser, monkeypatch):
    state = begin(browser)
    def token(url, **kwargs):
        assert kwargs['data']['code_verifier']
        return response({'access_token': 'private-token', 'refresh_token': 'private-refresh', 'scope': google.SCOPE, 'expires_in': 3600})
    monkeypatch.setattr(google.httpx, 'post', token)
    result = browser.get('/auth/google/callback', params={'state': state, 'code': 'test-code'}, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers['location'].endswith('calendar=connected')
    assert 'private-token' not in result.text + str(result.headers)
    return state


def test_missing_configuration(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, 'google_client_id', '')
    result = client.get('/auth/google/start', follow_redirects=False)
    assert result.headers['location'].endswith('calendar=not-configured')


def test_state_and_replay(browser, monkeypatch):
    state = begin(browser)
    assert browser.get('/auth/google/callback', params={'state': 'wrong', 'code': 'x'}).status_code == 400
    browser.app.state.calendar_sessions.pending[state]['expires'] = 0
    assert browser.get('/auth/google/callback', params={'state': state, 'code': 'x'}).status_code == 400
    state = authorize(browser, monkeypatch)
    assert browser.get('/auth/google/callback', params={'state': state, 'code': 'x'}).status_code == 400


def test_denial_and_scope(browser, monkeypatch):
    state = begin(browser)
    denied = browser.get('/auth/google/callback', params={'state': state, 'error': 'access_denied'}, follow_redirects=False)
    assert denied.headers['location'].endswith('calendar=denied')
    state = begin(browser)
    monkeypatch.setattr(google.httpx, 'post', lambda *a, **kw: response({'access_token': 'secret', 'scope': 'other'}))
    result = browser.get('/auth/google/callback', params={'state': state, 'code': 'x'}, follow_redirects=False)
    assert result.headers['location'].endswith('calendar=scope-denied')
    assert not browser.get('/calendar/connection').json()['connected']


def test_real_calendar_isolation_and_disconnect(browser, monkeypatch):
    authorize(browser, monkeypatch)
    now = datetime.now(timezone.utc)
    event = {'id': 'private-event', 'summary': 'Private class', 'location': 'PC 213',
             'start': {'dateTime': (now + timedelta(hours=1)).isoformat()},
             'end': {'dateTime': (now + timedelta(hours=2)).isoformat()}}
    monkeypatch.setattr(google.GoogleCalendar, 'list_events', lambda *args: [event])
    assert browser.get('/calendar/events').json()[0]['event_name'] == 'Private class'
    # A real connection must not silently use the shared demo home.
    assert browser.get('/calendar/next-event').json()['trip'] is None
    assert browser.get('/me').json()['home'] is None
    assert browser.put('/me', json={'name': 'Private name'}).status_code == 200
    assert browser.get('/me').json()['name'] == 'Private name'
    assert browser.get('/alerts').status_code == 200
    other = TestClient(browser.app)
    assert not other.get('/calendar/connection').json()['connected']
    assert other.get('/me').json()['name'] != 'Private name'
    assert all(e['event_id'] != 'private-event' for e in other.get('/calendar/events').json())
    assert browser.delete('/calendar/connection').status_code == 200
    assert browser.get('/calendar/events').status_code == 401
    assert browser.get('/alerts').status_code == 401
    assert browser.post('/calendar/demo').json()['source'] == 'demo'
    assert browser.get('/calendar/events').status_code == 200


def test_demo_origin_and_real_mode(browser, monkeypatch):
    assert browser.post('/calendar/demo', headers={'Origin': 'https://untrusted.example'}).status_code == 403
    monkeypatch.setattr(browser.app.state.settings, 'use_fake_calendar', False)
    assert browser.get('/calendar/events').status_code == 401
    assert browser.post('/calendar/demo').status_code == 200
    assert browser.get('/calendar/events').status_code == 200


def test_refresh_and_pagination(browser, monkeypatch):
    authorize(browser, monkeypatch)
    manager = browser.app.state.calendar_sessions
    connection = manager.get(browser.cookies.get(google.COOKIE))
    connection['tokens']['expires_at'] = 0
    def refresh(url, **kwargs):
        assert kwargs['data']['grant_type'] == 'refresh_token'
        return response({'access_token': 'refreshed', 'expires_in': 3600})
    monkeypatch.setattr(google.httpx, 'post', refresh)
    calls = []
    class FakeGoogleClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, params, headers):
            assert headers['Authorization'] == 'Bearer refreshed'
            calls.append(dict(params))
            return response({'items': [{'id': 'b'}]} if 'pageToken' in params else {'items': [{'id': 'a'}, {'id': 'cancelled', 'status': 'cancelled'}], 'nextPageToken': 'page2'})
    monkeypatch.setattr(google.httpx, 'Client', FakeGoogleClient)
    source = google.GoogleCalendar(browser.app.state.settings, connection, manager.lock)
    now = datetime.now(timezone.utc)
    assert source.list_events(now, now + timedelta(days=1)) == [{'id': 'a'}, {'id': 'b'}]
    assert calls[1]['pageToken'] == 'page2'
    assert connection['tokens']['refresh_token'] == 'private-refresh'
    connection['tokens']['expires_at'] = 0
    monkeypatch.setattr(google.httpx, 'post', lambda *a, **kw: response({'error': 'invalid_grant'}, 400))
    with pytest.raises(google.HTTPException) as caught:
        source.list_events(now, now + timedelta(days=1))
    assert caught.value.status_code == 401
    assert connection['tokens'] is None
