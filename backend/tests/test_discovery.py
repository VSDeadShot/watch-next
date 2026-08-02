"""Tests for the pool of things nobody here has watched.

Everything else in this app starts from somebody's history, which means every
title it knows about is one they have already seen. A recommender built on that
can only ever suggest a rewatch. This is where unwatched titles come from, and
the constraints on it are almost entirely about restraint: JustWatch has no
public API, discovery costs several requests, and what is popular in a country
does not change between two evenings.

No network: the pool is filled from a fake catalogue that records what it was
asked for.
"""

from datetime import UTC, datetime, timedelta

from simplejustwatchapi.exceptions import JustWatchApiError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiscoveryRun, Title
from app.services.discovery import (
    DEFAULT_PAGES,
    DEFAULT_TTL,
    pool_is_stale,
    refresh_pool,
)
from app.services.justwatch_client import CatalogueEntry, OfferEntry
from app.services.offers import cached_offers

NOW = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)


def entry(node_id: str, **overrides) -> CatalogueEntry:
    values = {
        "title": f"Title {node_id}",
        "object_type": "MOVIE",
        "release_year": 2021,
        "runtime_minutes": 105,
        "genres": ("cmy",),
        "imdb_score": 7.4,
        "offers": (OfferEntry(provider="nfx", monetization="FLATRATE"),),
    }
    return CatalogueEntry(node_id=node_id, **{**values, **overrides})


class FakeCatalogue:
    """Answers ``popular`` from a script, recording every call.

    Each element of ``pages`` is either a list to return or an exception to
    raise, so a test can describe "the third page times out". The last outcome
    repeats, so a test that does not care how many pages were asked for does not
    have to count them.
    """

    country = "IN"

    def __init__(self, *pages):
        self.pages = list(pages) or [[]]
        self.calls: list[dict] = []

    def popular(self, *, providers=None, object_types=None, count=50, offset=0):
        self.calls.append(
            {"providers": providers, "object_types": object_types, "count": count, "offset": offset}
        )
        outcome = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)


def runs(session: Session) -> list[DiscoveryRun]:
    return list(session.scalars(select(DiscoveryRun).order_by(DiscoveryRun.id)).all())


def titles(session: Session) -> list[Title]:
    return list(session.scalars(select(Title).order_by(Title.id)).all())


class TestStayingOffTheApi:
    def test_a_pool_that_has_never_been_filled_is_stale(self, session):
        assert pool_is_stale(session, country="IN", now=NOW)

    def test_a_pool_filled_just_now_is_not(self, session):
        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW)

        assert not pool_is_stale(session, country="IN", now=NOW)

    def test_a_pool_filled_long_enough_ago_is_stale_again(self, session):
        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW)

        assert pool_is_stale(session, country="IN", now=NOW + DEFAULT_TTL + timedelta(minutes=1))

    def test_a_fresh_pool_is_not_refilled(self, session):
        """The whole point. Refilling on every request would spend several
        requests against an unofficial API to learn that the same films are
        still popular."""
        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW)
        catalogue = FakeCatalogue([entry("tm2")])

        summary = refresh_pool(session, catalogue, now=NOW)

        assert summary.skipped
        assert catalogue.calls == []
        assert len(titles(session)) == 1

    def test_a_fresh_pool_can_still_be_refilled_on_purpose(self, session):
        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW)

        summary = refresh_pool(session, FakeCatalogue([entry("tm2")]), now=NOW, force=True)

        assert not summary.skipped
        assert len(titles(session)) == 2

    def test_another_country_has_its_own_pool(self, session):
        """Availability is per country, so a pool filled for one says nothing
        about another."""
        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW)

        assert pool_is_stale(session, country="US", now=NOW)


class TestWhatComesBack:
    def test_a_popular_title_becomes_a_catalogue_row(self, session):
        refresh_pool(session, FakeCatalogue([entry("tm1", title="Inception")]), now=NOW)

        [title] = titles(session)
        assert title.jw_node_id == "tm1"
        assert title.title == "Inception"
        assert title.genres == ["cmy"]
        assert title.imdb_score == 7.4

    def test_its_availability_is_cached_at_the_same_time(self, session):
        """The free ride again: popular returns the offers alongside the title,
        so the pool arrives already knowing where everything streams."""
        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW)

        [title] = titles(session)
        [offer] = cached_offers(session, title.id, country="IN")
        assert offer.provider == "nfx"

    def test_a_title_already_known_is_updated_rather_than_duplicated(self, session):
        refresh_pool(session, FakeCatalogue([entry("tm1", imdb_score=7.4)]), now=NOW)

        refresh_pool(
            session,
            FakeCatalogue([entry("tm1", imdb_score=8.1)]),
            now=NOW + DEFAULT_TTL * 2,
        )

        [title] = titles(session)
        assert title.imdb_score == 8.1

    def test_the_same_title_twice_in_one_answer_is_stored_once(self, session):
        """JustWatch is under no obligation to deduplicate across pages, and a
        unique violation here would abandon the whole pass."""
        refresh_pool(session, FakeCatalogue([entry("tm1"), entry("tm1")]), now=NOW)

        assert len(titles(session)) == 1

    def test_the_run_records_what_it_achieved(self, session):
        refresh_pool(session, FakeCatalogue([entry("tm1"), entry("tm2")]), now=NOW, pages=1)

        [run] = runs(session)
        assert run.titles_seen == 2
        assert run.titles_added == 2
        assert run.requests_made == 1
        assert run.country == "IN"

    def test_seeing_the_same_titles_again_adds_none(self, session):
        """A run that returns plenty and adds nothing means the pool is
        saturated, which is worth being able to tell from a run that returned
        nothing at all."""
        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW, pages=1)

        refresh_pool(session, FakeCatalogue([entry("tm1")]), now=NOW, pages=1, force=True)

        latest = runs(session)[-1]
        assert latest.titles_seen == 1
        assert latest.titles_added == 0


class TestHowManyRequestsItSpends:
    def test_it_pages_through_more_than_one_answer(self, session):
        """The pool is filtered hard afterwards -- by availability, by runtime,
        by everything already watched -- so a single page routinely leaves
        nothing to recommend."""
        catalogue = FakeCatalogue([entry("tm1")])

        refresh_pool(session, catalogue, now=NOW)

        assert len(catalogue.calls) >= DEFAULT_PAGES

    def test_each_page_asks_for_the_next_slice(self, session):
        catalogue = FakeCatalogue([entry("tm1")])

        refresh_pool(session, catalogue, now=NOW, pages=3, page_size=50)

        assert [call["offset"] for call in catalogue.calls[:3]] == [0, 50, 100]

    def test_the_pages_are_filtered_to_what_the_user_pays_for(self, session):
        catalogue = FakeCatalogue([entry("tm1")])

        refresh_pool(session, catalogue, subscriptions=["nfx"], now=NOW, pages=2)

        assert catalogue.calls[0]["providers"] == ["nfx"]
        assert catalogue.calls[1]["providers"] == ["nfx"]

    def test_one_unfiltered_page_is_fetched_as_well(self, session):
        """Filtering to somebody's services structurally cannot return anything
        free on a service they do not have -- and the product rule says those
        count. Without this page the pool could never contain one."""
        catalogue = FakeCatalogue([entry("tm1")])

        refresh_pool(session, catalogue, subscriptions=["nfx"], now=NOW, pages=2)

        assert [call["providers"] for call in catalogue.calls] == [["nfx"], ["nfx"], None]

    def test_with_no_subscriptions_that_page_is_not_fetched_twice(self, session):
        """With nothing to filter by, every page is already unfiltered, and a
        second identical request buys nothing at all."""
        catalogue = FakeCatalogue([entry("tm1")])

        refresh_pool(session, catalogue, subscriptions=[], now=NOW, pages=2)

        assert [call["providers"] for call in catalogue.calls] == [None, None]


class TestWhenJustWatchFails:
    def test_a_failure_partway_keeps_what_arrived_first(self, session):
        catalogue = FakeCatalogue([entry("tm1")], JustWatchApiError("nope"))

        summary = refresh_pool(session, catalogue, now=NOW, pages=3)

        assert summary.added == 1
        assert len(titles(session)) == 1

    def test_a_failure_stops_the_paging_rather_than_pressing_on(self, session):
        """A listing that just refused is unlikely to serve the next slice, and
        carrying on spends requests against an unofficial API to find that out.
        The pages that worked are kept; the rest wait for the next top-up."""
        catalogue = FakeCatalogue([entry("tm1")], JustWatchApiError("nope"), [entry("tm2")])

        refresh_pool(session, catalogue, now=NOW, pages=3)

        assert len(catalogue.calls) == 2
        assert [title.jw_node_id for title in titles(session)] == ["tm1"]

    def test_a_partial_run_still_counts_as_a_run(self, session):
        catalogue = FakeCatalogue([entry("tm1")], JustWatchApiError("nope"))

        refresh_pool(session, catalogue, now=NOW, pages=3)

        assert not pool_is_stale(session, country="IN", now=NOW)

    def test_a_run_that_learned_nothing_at_all_is_not_recorded(self, session):
        """Recording it would cache the outage: a transient failure would then
        block discovery for a whole day, which is exactly the wrong response to
        something that might work in a minute."""
        refresh_pool(session, FakeCatalogue(JustWatchApiError("down")), now=NOW)

        assert runs(session) == []
        assert pool_is_stale(session, country="IN", now=NOW)

    def test_a_total_failure_does_not_discard_the_callers_own_work(self, session):
        """This session belongs to whoever called, and they may well have
        pending work of their own. A service that rolls back a session it does
        not own destroys work it never knew about."""
        pending = Title(jw_node_id="tm-pending", object_type="MOVIE", title="Pending")
        session.add(pending)
        session.flush()

        refresh_pool(session, FakeCatalogue(JustWatchApiError("down")), now=NOW)

        assert [title.jw_node_id for title in titles(session)] == ["tm-pending"]

    def test_a_total_failure_is_reported_rather_than_raised(self, session):
        """A recommendation request that cannot top the pool up should still
        answer from the pool it has."""
        summary = refresh_pool(session, FakeCatalogue(JustWatchApiError("down")), now=NOW)

        assert summary.added == 0
        assert summary.failed

    def test_nothing_popular_is_not_a_failure(self, session):
        summary = refresh_pool(session, FakeCatalogue([]), now=NOW)

        assert not summary.failed
        assert summary.seen == 0
