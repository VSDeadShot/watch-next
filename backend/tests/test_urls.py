"""Tests for deciding whether a catalogue URL is one a browser can be sent to.

Every URL in this app arrives from JustWatch: the deep link on an offer, a
provider's icon, a poster. They go into an ``href`` or a ``src`` unchanged, and
nothing about an unofficial community API makes its strings trustworthy.

React 19 does block a ``javascript:`` href -- verified in the installed bundle,
which rewrites it to a thrown error, with a regex that tolerates interleaved
control characters -- so this is not a live cross-site-scripting hole. It is
still the app depending on a framework internal that React's own message calls
"a precaution", for a property this app can hold itself, on an API that is
published to more than one client.

Table-driven, one row per real-world-shaped string, because the interesting part
is the list of things somebody thought of.
"""

import pytest

from app.core.urls import CATALOGUE_IMAGE_HOST, is_catalogue_image_url, is_web_url

# Spelled out, because a literal backslash at the end of a string cannot be
# written raw and an escaped one is easy to misread as two characters.
BACKSLASH = chr(92)

FOLLOWABLE = [
    "https://www.netflix.com/title/70264888",
    "http://example.test/watch",
    # The scheme is case-insensitive in the standard, so it must be here too.
    "HTTPS://WWW.NETFLIX.COM/title/1",
    "https://x.test",
    "https://x.test:8080/a/b?c=d&e=f#g",
    # Percent-encoded and non-ASCII paths are ordinary, not suspicious.
    "https://x.test/a%20b",
    "https://x.test/café",
]

REFUSED = [
    # The one everybody means when they say this needs checking.
    "javascript:alert(1)",
    # `urlparse` lowercases the scheme, so a mixed-case attempt is the same
    # attempt. Asserted rather than assumed, because it is the whole reason a
    # scheme allowlist is enough on its own.
    "JavaScript:alert(1)",
    "JAVASCRIPT:alert(1)",
    # Tabs, newlines and nulls inside the scheme are stripped before parsing --
    # by Python, following the URL standard -- so these all arrive as
    # `javascript` too. The obfuscations a hand-written prefix check would miss.
    "java\tscript:alert(1)",
    "jav\nascript:alert(1)",
    "jav\rascript:alert(1)",
    "\x00javascript:alert(1)",
    " javascript:alert(1)",
    "\t javascript:alert(1)",
    # A hostile scheme that *does* carry an authority, which is the case the
    # host requirement cannot help with -- only the scheme allowlist refuses
    # these, and without them every rejection below could be explained by the
    # missing host instead. `//x.test/` reads as a comment to a JavaScript
    # parser and `%0a` ends it, which is what makes this shape a real payload
    # rather than a curiosity.
    "javascript://x.test/%0aalert(1)",
    "JaVaScRiPt://X.TEST/%0aalert(1)",
    "javascript://%0aalert(1)",
    "file://server/share/x",
    "data://x.test/,x",
    # Not script execution in a modern browser, but not a destination either.
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "blob:https://x.test/0-0",
    "mailto:someone@example.test",
    "tel:+1234",
    # Protocol-relative: no scheme at all, and would inherit ours.
    "//evil.test/x",
    # Relative and nonsense.
    "/watch/1",
    "watch/1",
    "not a url",
    # A scheme with nowhere to go. An `href` of "https://" is not a link.
    "https://",
    "http://",
    "https:///path",
    # Nothing at all.
    None,
    "",
    "   ",
    "\n",
]


CATALOGUE_IMAGES = [
    "https://images.justwatch.com/poster/302061947/s718/inception.jpg",
    "https://images.justwatch.com/icon/207360008/s100/netflix.png",
    # The host is case-insensitive, and `urlparse` lowercases it.
    "https://IMAGES.JUSTWATCH.COM/poster/1/s718/x.jpg",
    "https://images.justwatch.com/poster/1/s718/caf%C3%A9.jpg",
]

IMAGES_FROM_ELSEWHERE = [
    # The two shapes the library's concatenation admits. Both of these are what
    # you get by putting a string in a field documented to hold a path, and both
    # pass the link check, which is the entire argument for a second function.
    "https://images.justwatch.com@evil.test/x.jpg",
    "https://images.justwatch.com.evil.test/x.jpg",
    "https://images.justwatch.com%2F@evil.test/x.jpg",
    "https://images.justwatch.com@evil.test:443/x.jpg",
    # A subdomain is not the host either. It could be legitimate one day, at
    # which point this is a one-line change made on purpose rather than a hole
    # that was open the whole time.
    "https://cdn.images.justwatch.com/x.jpg",
    "https://justwatch.com/x.jpg",
    # An unrelated host, stated plainly so the list is not all trickery.
    "https://evil.test/x.jpg",
]


@pytest.mark.parametrize("url", FOLLOWABLE)
def test_an_ordinary_web_address_is_followable(url: str):
    assert is_web_url(url) is True


@pytest.mark.parametrize("url", REFUSED)
def test_anything_else_is_refused(url: str | None):
    assert is_web_url(url) is False


class TestControlCharactersInsideAnOtherwiseFineUrl:
    """The gap a scheme check alone leaves.

    `urlparse` strips tabs and newlines before parsing, so it reports scheme
    `https` and host `x.test` for a value that still *contains* the newline. The
    string is what gets written to the database and printed into a log line, so
    the check is on what arrived rather than on what parsing made of it.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.test/a\nb",
            "https://x.test/a\rb",
            "https://x.test/a\tb",
            "https://x.test/a\x00b",
            "https://x.test/a\x1fb",
            "https://x.test/a b",
        ],
    )
    def test_a_control_character_or_space_refuses_it(self, url: str):
        assert is_web_url(url) is False

    def test_the_same_url_without_it_is_fine(self):
        """So the case above is failing for the reason it says, and not because
        of something else about the address."""
        assert is_web_url("https://x.test/ab") is True


class TestAUrlThatCannotBeParsedAtAll:
    def test_an_unterminated_ipv6_host_is_refused_rather_than_raising(self):
        """`urlparse` raises for this one rather than returning something
        useless, which is the only input found that reaches the `except` branch
        -- so without this the handler is untested code that looks defensive."""
        assert is_web_url("https://[::1") is False

    def test_a_well_formed_ipv6_host_is_still_fine(self):
        assert is_web_url("https://[fe80::1]/x") is True


class TestItIsAPredicateAndNothingMore:
    def test_it_does_not_rewrite_anything(self):
        """Deliberately not a sanitiser. A rewritten URL is a URL nobody chose:
        it either still works, in which case the rewrite was unnecessary, or it
        quietly points somewhere else. The caller drops what this refuses, and
        every render site already has a state for a missing link."""
        assert not hasattr(is_web_url("https://x.test"), "startswith")
        assert is_web_url("https://x.test") is True


class TestWhereTheTwoUrlStandardsDisagree:
    r"""The cases RFC 3986 and the WHATWG URL Standard read differently.

    `urlparse` implements the RFC; the browser -- and `new URL`, which the
    frontend mirror uses -- implements WHATWG. On a backslash or an empty
    authority they do not merely differ in strictness, they disagree about
    **which host the URL names**. Measured, not guessed:

        https://good.test\@evil.test/    RFC host: good.test\@evil.test
                                         WHATWG:   good.test
        https:/\evil.test/               RFC host: (none)
                                         WHATWG:   evil.test
        https:///path                    RFC host: (none)
                                         WHATWG:   path

    Whatever this module decides, the browser is what actually navigates. So
    rather than pick a winner, both shapes are refused: no legitimate URL
    carries an unescaped backslash -- it is `%5C` -- and no legitimate URL has
    an empty authority. Refusing costs nothing real and keeps this check and the
    browser talking about the same host, which is what the next thing to use
    this function, a host allowlist for images, will depend on.
    """

    @pytest.mark.parametrize(
        "url",
        [
            r"https://good.test\@evil.test/x",
            r"https://good.test\.evil.test/",
            "https:/" + BACKSLASH + "evil.test/",
            "https://evil.test" + BACKSLASH,
            r"https://x.test/a\b",
        ],
    )
    def test_a_backslash_anywhere_refuses_it(self, url: str):
        assert is_web_url(url) is False

    def test_a_percent_encoded_backslash_is_fine(self):
        """Which is how a URL that genuinely wants one spells it, and the reason
        refusing the raw character costs nothing."""
        assert is_web_url("https://x.test/a%5Cb") is True

    @pytest.mark.parametrize("url", ["https:///path", "https:////a", "http:///x"])
    def test_an_empty_authority_refuses_it(self, url: str):
        assert is_web_url(url) is False


class TestAnImageIsHeldToOneHost:
    """A poster and a provider icon are held to the host they come from.

    A link and an image are not the same risk. A link needs a click; an ``src``
    is fetched the moment the page renders, so a hostile host is handed the
    viewer's IP address and user agent with no action on their part -- and this
    app's whole subject matter is what somebody watches.

    The reason the check is affordable is that the host is not really JustWatch's
    to choose. The client library *builds* both fields by string concatenation
    onto a constant of its own -- ``_IMAGES_URL + json["posterUrl"]`` -- so a
    URL naming any other host is already a URL nobody intended. And it is that
    same concatenation that makes it forgeable, because nothing separates the
    constant from the field:

        posterUrl = "/poster/1/s718/x.jpg"  ->  host images.justwatch.com
        posterUrl = "@evil.test/x.jpg"      ->  host evil.test
        posterUrl = ".evil.test/x.jpg"      ->  host images.justwatch.com.evil.test

    ``frontend/next.config.ts`` already commits to exactly this host for the two
    posters, which go through ``next/image``; measured, its optimiser answers
    400 to every off-list host before making any outbound request. So this is
    not new policy. It extends the policy that already exists to the two places
    it does not reach: the value we store, and the provider icon, which is a
    deliberate plain ``<img>`` and therefore goes nowhere near the optimiser.
    """

    @pytest.mark.parametrize("url", CATALOGUE_IMAGES)
    def test_an_image_from_the_catalogue_host_is_usable(self, url: str):
        assert is_catalogue_image_url(url) is True

    @pytest.mark.parametrize("url", IMAGES_FROM_ELSEWHERE)
    def test_any_other_host_is_refused(self, url: str):
        assert is_catalogue_image_url(url) is False

    @pytest.mark.parametrize("url", IMAGES_FROM_ELSEWHERE)
    def test_the_link_check_would_have_allowed_every_one_of_them(self, url: str):
        """The other half of the case above.

        Without this, a reader cannot tell whether the host check is doing the
        work or whether `is_web_url` was already refusing these for some other
        reason -- and if it were, the whole class would be testing nothing.
        """
        assert is_web_url(url) is True

    def test_plain_http_from_the_right_host_is_refused(self):
        """Which `is_web_url` allows, and the image optimiser does not.

        `next.config.ts` names a protocol as well as a hostname, and measuring
        it confirmed `http://images.justwatch.com/x.jpg` answers 400. Every real
        URL from this host is https, so matching the stricter of the two rules
        costs nothing and keeps the backend and the optimiser saying the same
        thing about the same string."""
        assert is_web_url("http://images.justwatch.com/x.jpg") is True
        assert is_catalogue_image_url("http://images.justwatch.com/x.jpg") is False

    def test_a_trailing_dot_on_the_host_is_refused(self):
        """A fully qualified name a browser would treat as the same host.

        Refused anyway, and worth being clear that this one is not an attack --
        it is the cost of comparing the authority as a string. Nothing produces
        this shape, and the alternative is a normalization step, which is how a
        host check starts disagreeing with the browser it is protecting."""
        assert is_catalogue_image_url("https://images.justwatch.com./x.jpg") is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://images.justwatch.com:8443/x.jpg",
            "https://images.justwatch.com:443/x.jpg",
            "https://user:pw@images.justwatch.com/x.jpg",
        ],
    )
    def test_a_port_or_userinfo_is_refused_even_naming_the_right_host(self, url: str):
        """The two places this is stricter than the browser, on purpose.

        `new URL` in the frontend mirror reports the host it will connect to, so
        it drops a default port and resolves userinfo away and accepts both of
        these -- running the two implementations over the same sixty-two cases
        is how that was found rather than assumed. Neither is a hole: they do
        fetch from the allowlisted host.

        Refused here anyway, because comparing the authority as one string is
        the version with no normalization step in it, and normalization is how a
        host check starts disagreeing with the browser it is protecting. Being
        stricter than necessary in two shapes nothing produces is the cheaper
        mistake, and the divergence only ever runs in this direction.
        """
        assert is_catalogue_image_url(url) is False

    @pytest.mark.parametrize("url", [*REFUSED, "https://images.justwatch.com/a b.jpg"])
    def test_it_still_refuses_everything_a_link_would(self, url: str | None):
        """The host rule is added to the link rule, not swapped for it. A
        `javascript:` URL does not become acceptable by having the right
        authority -- `javascript://images.justwatch.com/%0aalert(1)` is in the
        table this reuses."""
        assert is_catalogue_image_url(url) is False

    def test_the_host_is_the_one_the_client_library_builds_from(self):
        """Pinned to the installed library rather than to a comment.

        Both fields arrive as `_IMAGES_URL + path`, so our constant and theirs
        have to be the same string or every image in the app disappears at once.
        Read out of the library so that a change on their side fails one test
        with a legible message, instead of being discovered as a page of broken
        images.
        """
        try:
            from simplejustwatchapi.query import _IMAGES_URL
        except ImportError:  # pragma: no cover -- the point of the message
            pytest.fail(
                "simplejustwatchapi.query._IMAGES_URL is gone. It is where both "
                "poster_url and icon_url get their host, so find what replaced "
                "it and check CATALOGUE_IMAGE_HOST still matches."
            )
        ours = f"https://{CATALOGUE_IMAGE_HOST}"
        assert ours == _IMAGES_URL
