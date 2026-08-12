"""Tests for the Netflix import endpoint.

These go through the real app with a real (in-memory) database. The only thing
swapped out is which database the request handler is handed.
"""

import io
import zipfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models import ImportRun

ENDPOINT = "/api/imports/netflix"


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def upload(client: TestClient, data: bytes, filename: str = "ViewingActivity.csv"):
    return client.post(ENDPOINT, files={"file": (filename, data, "text/csv")})


class TestUploadingAnExport:
    def test_returns_the_import_summary(self, client: TestClient, full_export: bytes):
        response = upload(client, full_export)

        assert response.status_code == 200
        body = response.json()
        assert body["imported"] == 4
        assert body["total_rows"] == 7
        assert body["export_format"] == "full"

    def test_the_summary_reconciles_against_the_file(self, client: TestClient, full_export: bytes):
        body = upload(client, full_export).json()

        assert body["imported"] + body["duplicates"] + body["skipped"] == body["total_rows"]

    def test_explains_every_skipped_row(self, client: TestClient, full_export: bytes):
        body = upload(client, full_export).json()

        assert body["skipped_by_reason"] == {"supplemental_video": 2, "too_short": 1}

    def test_records_the_uploaded_filename(self, client: TestClient, full_export: bytes):
        body = upload(client, full_export, filename="my-netflix-history.csv").json()

        assert body["filename"] == "my-netflix-history.csv"

    def test_the_same_upload_twice_imports_nothing_new(
        self, client: TestClient, full_export: bytes
    ):
        upload(client, full_export)
        body = upload(client, full_export).json()

        assert body["imported"] == 0
        assert body["duplicates"] == 4

    def test_accepts_the_personal_data_zip_unopened(self, client: TestClient, full_export: bytes):
        """The useful file sits among a dozen folders; making the user find it
        is exactly the friction this project exists to remove."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("netflix-report/CONTENT_INTERACTION/ViewingActivity.csv", full_export)

        response = upload(client, buffer.getvalue(), filename="netflix-report.zip")

        assert response.status_code == 200
        assert response.json()["imported"] == 4

    def test_surfaces_date_assumptions(self, client: TestClient):
        body = upload(client, b"Title,Date\nInception,01/02/2024\n").json()

        assert body["assumptions"]


class TestRejectingBadUploads:
    def test_a_file_that_is_not_an_export_is_a_client_error(self, client: TestClient):
        response = upload(client, b"name,email\nSam,sam@example.com\n")

        assert response.status_code == 400

    def test_the_error_says_what_was_wrong_with_the_file(self, client: TestClient):
        """A bare 500 leaves the user guessing which of a dozen files to try."""
        detail = upload(client, b"name,email\nSam,sam@example.com\n").json()["detail"]

        assert "Title" in detail
        assert "ViewingActivity.csv" in detail

    def test_a_corrupt_archive_is_a_client_error(self, client: TestClient):
        response = upload(client, b"PK\x03\x04nonsense", filename="broken.zip")

        assert response.status_code == 400

    def test_uploading_no_file_is_rejected(self, client: TestClient):
        assert client.post(ENDPOINT).status_code == 422


class TestRefusingAnUploadThatIsTooLarge:
    """Two limits, because they bound different things.

    `MAX_UPLOAD_BYTES` is what the endpoint will accept at all. A real Netflix
    download is a zip of about a dozen folders, most of which have nothing to do
    with viewing history, so the archive is legitimately larger than the file
    wanted out of it. `MAX_HISTORY_BYTES` is what gets decompressed and parsed,
    and it is the one that decides how much memory a request can cost.
    """

    UPLOAD_CAP = 4 * 1024
    HISTORY_CAP = 1024

    @pytest.fixture
    def client(self, session: Session) -> Iterator[TestClient]:
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_settings] = lambda: Settings(
            max_upload_bytes=self.UPLOAD_CAP, max_history_bytes=self.HISTORY_CAP
        )
        with TestClient(app) as client:
            yield client
        app.dependency_overrides.clear()

    @pytest.fixture
    def loose_client(self, session: Session) -> Iterator[TestClient]:
        """Both limits at the upload cap.

        The only way to watch the upload check's boundary on its own: under this
        class's much tighter history cap, a file of exactly `UPLOAD_CAP` bytes
        would be refused a step later for a real reason, and the test could not
        tell that from an off-by-one in the check it means to be about.
        """
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_settings] = lambda: Settings(
            max_upload_bytes=self.UPLOAD_CAP, max_history_bytes=self.UPLOAD_CAP
        )
        with TestClient(app) as client:
            yield client
        app.dependency_overrides.clear()

    def test_an_upload_of_exactly_the_limit_is_accepted(self, loose_client: TestClient):
        """The boundary from the side that must not be refused. Rejecting a file
        for being one byte too large reads to the user as data loss."""
        head, tail = b"Title,Date\n", b",2024-01-01\n"
        data = head + b"x" * (self.UPLOAD_CAP - len(head) - len(tail)) + tail
        assert len(data) == self.UPLOAD_CAP

        response = upload(loose_client, data)

        assert response.status_code == 200

    def test_an_upload_one_byte_over_the_limit_is_refused(self, loose_client: TestClient):
        response = upload(loose_client, b"x" * (self.UPLOAD_CAP + 1))

        assert response.status_code == 413

    def test_an_upload_past_the_limit_is_refused(self, client: TestClient):
        response = upload(client, b"Title,Date\n" + b"x" * self.UPLOAD_CAP)

        assert response.status_code == 413

    def test_an_upload_within_the_limit_is_not(self, client: TestClient):
        response = upload(client, b"Title,Date\nInception,2024-01-01\n")

        assert response.status_code == 200

    def test_an_archive_that_unpacks_past_the_limit_is_refused(self, client: TestClient):
        """Small enough to accept, far too large to read: the case the upload
        limit alone cannot see, because compression hides it."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("CONTENT_INTERACTION/ViewingActivity.csv", b"\0" * (1024 * 1024))
        archive_bytes = buffer.getvalue()
        assert len(archive_bytes) < self.UPLOAD_CAP  # it got past the first limit

        response = upload(client, archive_bytes, filename="netflix-report.zip")

        assert response.status_code == 413

    def test_the_refusal_says_which_limit_was_hit(self, client: TestClient):
        detail = upload(client, b"Title,Date\n" + b"x" * self.UPLOAD_CAP).json()["detail"]

        assert str(self.UPLOAD_CAP) in detail.replace(",", "")

    def test_a_refused_upload_leaves_nothing_behind(self, client: TestClient, session: Session):
        """A rejected import must not leave a half-written audit row: the whole
        point of the counts on `imports` is that they can be trusted."""
        upload(client, b"Title,Date\n" + b"x" * self.UPLOAD_CAP)

        assert session.query(ImportRun).count() == 0

    def test_a_file_that_is_merely_unreadable_is_still_a_400(self, client: TestClient):
        """The two refusals must stay distinguishable. 413 says "send less";
        400 says "send something else", and they are not the same advice."""
        response = upload(client, b"name,email\nSam,sam@example.com\n")

        assert response.status_code == 400
