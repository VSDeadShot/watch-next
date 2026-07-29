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

from app.db import get_db
from app.main import app

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
