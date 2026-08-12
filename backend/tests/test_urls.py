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

from app.core.urls import is_web_url

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
