"""Tests for the YouTube import endpoint.

Through the real app with a real (in-memory) database. Worth having separately
from the service tests because the endpoint is where the streaming could quietly
be undone -- it hands the upload's own file object to the reader rather than
awaiting the bytes, and that only works because the handler is sync and FastAPI
gives it a worker thread.
"""

import io
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.youtube_parser import YouTubeExportError
from app.db import get_db
from app.main import app
from app.models import ImportRun, YouTubeView

ENDPOINT = "/api/imports/youtube"


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def entry(**overrides) -> dict:
    entry = {
        "header": "YouTube",
        "title": "Watched A perfectly ordinary video",
        "titleUrl": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "subtitles": [{"name": "Some Channel"}],
        "time": "2024-03-14T20:12:03.415Z",
        "products": ["YouTube"],
        "activityControls": ["YouTube watch history"],
    }
    entry.update(overrides)
    return entry


def video(index: int) -> dict:
    return entry(
        title=f"Watched Video number {index}",
        titleUrl=f"https://www.youtube.com/watch?v=vid{index:08d}",
        time=f"2024-03-14T20:{index % 60:02d}:03.000Z",
    )


def upload(client: TestClient, entries: list[dict], filename: str = "watch-history.json"):
    data = json.dumps(entries).encode("utf-8")
    return client.post(ENDPOINT, files={"file": (filename, data, "application/json")})


class TestUploadingAHistory:
    def test_returns_the_import_summary(self, client: TestClient):
        response = upload(client, [video(1), video(2)])

        assert response.status_code == 200
        body = response.json()
        assert body["imported"] == 2
        assert body["total_rows"] == 2
        assert body["source"] == "youtube"
        assert body["export_format"] == "takeout"

    def test_the_summary_reconciles_against_the_file(self, client: TestClient):
        body = upload(client, [video(1), entry(titleUrl=None), entry(time="nonsense")]).json()

        assert body["imported"] + body["duplicates"] + body["skipped"] == body["total_rows"]

    def test_names_every_exclusion(self, client: TestClient):
        body = upload(
            client, [entry(titleUrl=None), entry(details=[{"name": "From Google Ads"}])]
        ).json()

        assert body["skipped_by_reason"] == {"unavailable_video": 1, "advert": 1}

    def test_keeps_the_filename_it_was_given(self, client: TestClient):
        body = upload(client, [video(1)], filename="watch-history.json").json()

        assert body["filename"] == "watch-history.json"

    def test_uploading_the_same_export_again_adds_nothing(self, client: TestClient):
        upload(client, [video(1), video(2)])

        body = upload(client, [video(1), video(2)]).json()

        assert body["imported"] == 0
        assert body["duplicates"] == 2

    def test_reads_a_history_of_a_few_thousand_views(self, client: TestClient, session: Session):
        """Through the endpoint, so the upload's spooled file is what gets
        streamed rather than a tidy in-memory buffer."""
        body = upload(client, [video(n) for n in range(2000)]).json()

        assert body["imported"] == 2000
        assert session.scalar(select(func.count()).select_from(YouTubeView)) == 2000


class TestTheWrongFile:
    def test_the_html_export_is_refused_with_a_way_forward(self, client: TestClient):
        """Takeout hands out HTML unless you ask for JSON, so this is the most
        likely wrong upload there is. A 400 that only says "bad file" would
        leave somebody stuck at the one step they cannot guess."""
        response = client.post(
            ENDPOINT,
            files={"file": ("watch-history.html", b"<!DOCTYPE html><html>", "text/html")},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "JSON" in detail
        assert "Takeout" in detail

    def test_a_netflix_csv_is_refused_rather_than_half_read(self, client: TestClient):
        response = client.post(
            ENDPOINT,
            files={
                "file": ("ViewingActivity.csv", b"Title,Date\nInception,2024-01-01\n", "text/csv")
            },
        )

        assert response.status_code == 400

    def test_a_truncated_download_stores_nothing(self, client: TestClient, session: Session):
        """The break is only found part way through, with rows already pending.
        Nothing may survive it -- least of all an audit row claiming an import
        happened."""
        good = json.dumps([video(n) for n in range(800)]).encode("utf-8")

        response = client.post(
            ENDPOINT,
            files={"file": ("watch-history.json", good[: len(good) // 2], "application/json")},
        )

        assert response.status_code == 400
        assert session.scalar(select(func.count()).select_from(YouTubeView)) == 0
        assert session.scalar(select(func.count()).select_from(ImportRun)) == 0

    def test_an_empty_upload_is_refused(self, client: TestClient):
        response = client.post(
            ENDPOINT, files={"file": ("watch-history.json", b"", "application/json")}
        )

        assert response.status_code == 400

    def test_a_rejected_upload_does_not_break_the_next_one(self, client: TestClient):
        client.post(ENDPOINT, files={"file": ("x.json", b"nonsense", "application/json")})

        body = upload(client, [video(1)]).json()

        assert body["imported"] == 1


class TestItDoesNotTouchTheCatalogue:
    def test_youtube_views_never_become_watch_events(self, client: TestClient, session: Session):
        """The separation is the product decision: nobody needs an app to tell
        them to watch YouTube. A table these cannot be written to is a stronger
        guarantee than remembering not to."""
        from app.models import WatchEvent

        upload(client, [video(1), video(2)])

        assert session.scalar(select(func.count()).select_from(WatchEvent)) == 0


def test_the_endpoint_hands_over_a_stream_not_the_bytes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """`await file.read()` would pull the whole history into memory and undo
    every bit of the streaming underneath it. It is one word away at all times
    and nothing else here would notice, because a buffered import behaves
    identically on the small files a test suite uses."""
    from app.api import imports

    seen: list[object] = []

    def capture(session, stream, **kwargs):
        seen.append(stream)
        raise YouTubeExportError("stopping here")

    monkeypatch.setattr(imports, "import_youtube_export", capture)
    upload(client, [video(1)])

    handed_over = seen[0]
    assert hasattr(handed_over, "read")
    # The specific regression: reading the upload and wrapping the bytes back up
    # would still be stream-shaped, and would still have cost the memory.
    assert not isinstance(handed_over, bytes | bytearray | io.BytesIO)
