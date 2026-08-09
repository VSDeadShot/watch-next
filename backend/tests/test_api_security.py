"""Tests for the shared-secret gate in front of the API.

This app has one user and no login, which was right for something that only
ever answered on localhost. Deployed, the same design means anyone holding the
URL can read a viewing history, change which services it thinks you pay for and
spend the JustWatch budget -- and CORS stops none of that, being a browser
policy rather than access control.

The gate is therefore a shared secret the Next.js proxy holds and the browser
never sees. It is off unless configured, so running this locally needs no
setup and behaves exactly as it did before.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.main import app

SECRET = "a-secret-the-browser-never-sees"


def build(session: Session, **settings) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_settings] = lambda: Settings(
        jw_country="IN", jw_language="en", **settings
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def open_client(session: Session) -> Iterator[TestClient]:
    """No secret configured, which is how this runs on a laptop."""
    yield from build(session)


@pytest.fixture
def guarded(session: Session) -> Iterator[TestClient]:
    yield from build(session, api_secret=SECRET)


class TestWithNoSecretConfigured:
    """The local default. Turning the gate on must be a deployment decision, not
    something that breaks every developer's checkout the day it lands."""

    def test_a_request_with_no_key_is_answered(self, open_client: TestClient):
        assert open_client.get("/api/stats").status_code == 200

    def test_a_stray_key_is_ignored_rather_than_refused(self, open_client: TestClient):
        response = open_client.get("/api/stats", headers={"X-API-Key": "anything at all"})
        assert response.status_code == 200


class TestWithASecretConfigured:
    def test_a_request_with_no_key_is_refused(self, guarded: TestClient):
        assert guarded.get("/api/stats").status_code == 401

    def test_a_wrong_key_is_refused(self, guarded: TestClient):
        assert guarded.get("/api/stats", headers={"X-API-Key": "wrong"}).status_code == 401

    def test_the_right_key_is_answered(self, guarded: TestClient):
        response = guarded.get("/api/stats", headers={"X-API-Key": SECRET})
        assert response.status_code == 200

    def test_the_refusal_says_what_is_missing(self, guarded: TestClient):
        # A bare 401 in a proxy's logs is indistinguishable from the backend
        # being broken. This is read by whoever is wiring the two together.
        assert "key" in guarded.get("/api/stats").json()["detail"].lower()

    def test_a_key_that_is_a_prefix_of_the_secret_is_refused(self, guarded: TestClient):
        # Guards the comparison itself: `startswith` or a truncated compare
        # would let this through.
        response = guarded.get("/api/stats", headers={"X-API-Key": SECRET[:-1]})
        assert response.status_code == 401


class TestWhatTheGateDoesNotCover:
    def test_health_answers_without_a_key(self, guarded: TestClient):
        """Render polls this to decide whether the service is up. Behind the
        gate, a healthy deployment would look permanently broken."""
        assert guarded.get("/health").status_code == 200


class TestEveryRouterIsBehindIt:
    """A gate applied router by router is a gate somebody can forget to apply to
    the next one. This fails when a router is added without it."""

    def test_no_api_route_answers_without_a_key(self, guarded: TestClient):
        unguarded = []
        for path, operations in app.openapi()["paths"].items():
            if not path.startswith("/api/"):
                continue
            for method in operations:
                # A concrete id, so a path parameter does not 422 before the
                # gate has had a chance to refuse.
                url = path.replace("{resolution_id}", "1").replace("{title_id}", "1")
                response = guarded.request(method.upper(), url)
                if response.status_code != 401:
                    unguarded.append(f"{method.upper()} {path} -> {response.status_code}")
        assert unguarded == []
