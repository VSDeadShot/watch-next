"""Every image URL leaving this API is held to the host it should have come from.

The client refuses a poster or an icon from anywhere else as it arrives, so this
is the second of two checks on the same rule -- the arrangement `core.availability`
already has with the deep link. The reason it is worth having twice is sharper
here than it is there: an offer expires after a week, so a bad one written before
the check existed would be replaced on its own. A title and a provider have no
TTL. A poster stored before any of this is served for as long as the row lives,
and nothing in the app would ever go back and look at it again.

Two kinds of test, and both are needed. The behavioural ones prove the rule
fires; the structural one proves nothing escaped it, which is the failure mode a
behavioural test cannot see -- a sixth response model, added later, carrying a
sixth `poster_url` that nobody thought about.
"""

from datetime import UTC, datetime

import pytest

from app import schemas
from app.core.urls import CATALOGUE_IMAGE_HOST

IMAGE_FIELDS = frozenset({"poster_url", "icon_url"})

ORDINARY = f"https://{CATALOGUE_IMAGE_HOST}/poster/302061947/s718/inception.jpg"

# What the client library builds from a `posterUrl` field of "@evil.test/x.jpg":
# its own host constant, then the field, with nothing in between. Every part of
# this is well formed except which host it names.
MOVED = f"https://{CATALOGUE_IMAGE_HOST}@evil.test/x.jpg"


def image_fields_in(model: type[schemas.BaseModel]) -> list[str]:
    return [name for name in model.model_fields if name in IMAGE_FIELDS]


def models_with_an_image() -> list[type[schemas.BaseModel]]:
    """Every response model in the module carrying one of these fields.

    Found by walking the module rather than by listing them, so that adding a
    model is enough to bring it under the structural test below -- a list would
    have to be remembered, which is the thing being guarded against.
    """
    return sorted(
        (
            value
            for value in vars(schemas).values()
            if isinstance(value, type)
            and issubclass(value, schemas.BaseModel)
            and image_fields_in(value)
        ),
        key=lambda model: model.__name__,
    )


class TestTheRuleFires:
    """On the two models whose fields are actually rendered as an image."""

    def test_a_recommendation_drops_a_poster_from_another_host(self):
        answer = schemas.RecommendedTitleResponse(
            title_id=1,
            jw_node_id="tm12345",
            title="Inception",
            object_type="MOVIE",
            score=8.4,
            poster_url=MOVED,
        )

        assert answer.poster_url is None

    def test_the_recommendation_itself_survives(self):
        """The product constraint outranks the poster. A card with a placeholder
        on it still answers the question; a request that fails because a poster
        was wrong answers nothing, and this API has exactly one answer to give."""
        answer = schemas.RecommendedTitleResponse(
            title_id=1,
            jw_node_id="tm12345",
            title="Inception",
            object_type="MOVIE",
            score=8.4,
            poster_url=MOVED,
        )

        assert answer.title == "Inception"
        assert answer.score == 8.4

    def test_a_provider_drops_an_icon_from_another_host(self):
        provider = schemas.ProviderResponse(
            short_name="nfx", name="Netflix", technical_name="netflix", icon_url=MOVED
        )

        assert provider.icon_url is None
        # And the tile is still selectable, which is what a settings page is for.
        assert provider.short_name == "nfx"

    def test_an_ordinary_poster_is_passed_through_unchanged(self):
        """The half that fails loudly if the rule is too strict. Without it, a
        validator that returned None for everything would pass every test above
        and be discovered as an app with no artwork in it."""
        answer = schemas.RecommendedTitleResponse(
            title_id=1,
            jw_node_id="tm12345",
            title="Inception",
            object_type="MOVIE",
            score=8.4,
            poster_url=ORDINARY,
        )

        assert answer.poster_url == ORDINARY

    def test_it_fires_when_the_model_is_built_from_a_row_as_well(self):
        """Which is how the routers build these -- keyword arguments off an ORM
        object, not `model_validate` on a parsed body. An `AfterValidator` runs
        for both, and this asserts it rather than assuming it."""
        assert (
            schemas.WatchlistItemResponse(
                title_id=1,
                jw_node_id="tm1",
                title="Inception",
                object_type="MOVIE",
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
                poster_url=MOVED,
            ).poster_url
            is None
        )


class TestNothingEscapesIt:
    def test_the_sweep_finds_the_models_that_exist_today(self):
        """A structural test whose search silently returns nothing passes for
        the wrong reason for ever. Five models carry one of these fields; this
        fails if that becomes zero, and is meant to be edited when it grows."""
        found = {model.__name__ for model in models_with_an_image()}

        assert found == {
            "ManualResolutionResponse",
            "ProviderResponse",
            "RecommendedTitleResponse",
            "ResolvedTitleResponse",
            "WatchlistItemResponse",
        }

    @pytest.mark.parametrize("model", models_with_an_image(), ids=lambda model: model.__name__)
    def test_every_image_field_carries_the_check(self, model: type[schemas.BaseModel]):
        """Declared as `CatalogueImageUrl`, not as a bare `str | None`.

        Read off the field's annotated metadata rather than by calling the model,
        because the point is the declaration: a field that happens to hold a
        valid value today is not a field that refuses an invalid one tomorrow.
        """
        for name in image_fields_in(model):
            validators = [
                getattr(entry, "func", None) for entry in model.model_fields[name].metadata
            ]
            assert schemas._only_from_the_catalogue_host in validators, (
                f"{model.__name__}.{name} is a plain string. Annotate it as "
                f"CatalogueImageUrl so it is checked on the way out like the rest."
            )
