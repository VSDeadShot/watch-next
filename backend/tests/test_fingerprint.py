"""Tests for the row fingerprint that makes re-importing an export idempotent."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.fingerprint import fingerprint_events, watch_event_fingerprint
from app.core.netflix_parser import RawWatchEvent

WATCHED_AT = datetime(2024, 3, 14, 20, 12, 3, tzinfo=UTC)


def event(
    raw_title: str = "Inception",
    watched_at: datetime = WATCHED_AT,
    profile_name: str | None = "Sam",
) -> RawWatchEvent:
    return RawWatchEvent(raw_title=raw_title, watched_at=watched_at, profile_name=profile_name)


def fingerprint(**overrides: object) -> str:
    fields: dict[str, object] = {
        "source": "netflix",
        "raw_title": "Inception",
        "watched_at": WATCHED_AT,
        "profile_name": "Sam",
    }
    fields.update(overrides)
    return watch_event_fingerprint(**fields)  # type: ignore[arg-type]


class TestFingerprintShape:
    def test_is_a_sha256_hex_digest(self):
        assert len(fingerprint()) == 64
        assert set(fingerprint()) <= set("0123456789abcdef")

    def test_is_stable_across_calls(self):
        assert fingerprint() == fingerprint()


class TestFingerprintDistinguishes:
    """Every field that identifies a viewing session must change the digest."""

    @pytest.mark.parametrize(
        ("field", "other"),
        [
            ("source", "youtube"),
            ("raw_title", "Arrival"),
            ("watched_at", datetime(2024, 3, 14, 20, 12, 4, tzinfo=UTC)),
            ("profile_name", "Alex"),
            ("occurrence", 1),
        ],
    )
    def test_changing_a_field_changes_the_digest(self, field: str, other: object):
        assert fingerprint(**{field: other}) != fingerprint()

    def test_field_boundaries_cannot_be_forged(self):
        """Content must not be able to impersonate the separator.

        Naive joining makes profile "Sam|Inception" with title "Arrival"
        indistinguishable from profile "Sam" with title "Inception|Arrival",
        so one row would silently swallow another.
        """
        assert fingerprint(profile_name="Sam|Inception", raw_title="Arrival") != fingerprint(
            profile_name="Sam", raw_title="Inception|Arrival"
        )

    def test_the_same_moment_in_another_zone_is_the_same_row(self):
        """Fingerprints compare instants, not wall clocks.

        Netflix's full export is already UTC, but a source that reports local
        time would otherwise re-import its whole history as new rows.
        """
        india = timezone(timedelta(hours=5, minutes=30))
        assert fingerprint(watched_at=WATCHED_AT.astimezone(india)) == fingerprint()


class TestFingerprintRejectsNaiveTimestamps:
    def test_a_naive_timestamp_is_refused(self):
        """A datetime with no zone would be read as the server's local time.

        That makes the digest depend on where the import ran: the same export
        loaded on a laptop in IST and on a UTC server would agree about nothing
        and re-insert every row. Refusing is the only safe reading.
        """
        with pytest.raises(ValueError, match="timezone"):
            fingerprint(watched_at=datetime(2024, 3, 14, 20, 12, 3))


class TestFingerprintTreatsAbsentProfileAsBlank:
    def test_missing_and_empty_profile_agree(self):
        """A blank cell and an absent column both mean "no profile"."""
        assert fingerprint(profile_name=None) == fingerprint(profile_name="")


class TestFingerprintEvents:
    def test_distinct_events_get_distinct_fingerprints(self):
        events = [event("Inception"), event("Arrival")]
        assert len(set(fingerprint_events(events, source="netflix"))) == 2

    def test_identical_rows_are_kept_apart(self):
        """The simple export has no clock time, so a same-day rewatch is a
        genuine second row rather than a duplicate to collapse."""
        events = [event(), event()]
        assert len(set(fingerprint_events(events, source="netflix"))) == 2

    def test_reparsing_the_same_file_yields_the_same_fingerprints(self):
        events = [event("Inception"), event(), event()]
        assert fingerprint_events(events, source="netflix") == fingerprint_events(
            events, source="netflix"
        )

    def test_a_later_export_keeps_earlier_fingerprints_stable(self):
        """Netflix exports are cumulative and newest-first, so a re-download is
        the previous file with rows prepended. Nothing already imported may
        change its fingerprint, or every old row would be inserted twice."""
        first = [event("Inception"), event("Arrival")]
        second = [event("Dune"), *first]

        assert set(fingerprint_events(first, source="netflix")) <= set(
            fingerprint_events(second, source="netflix")
        )

    def test_a_new_copy_of_a_repeated_row_only_adds_one_fingerprint(self):
        first = [event(), event()]
        second = [event(), event(), event()]

        earlier = fingerprint_events(first, source="netflix")
        later = fingerprint_events(second, source="netflix")

        assert set(earlier) <= set(later)
        assert len(set(later) - set(earlier)) == 1
