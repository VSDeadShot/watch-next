"""Whether a URL from the catalogue is one a browser can be sent to.

Every URL this app holds comes from JustWatch -- an offer's deep link, a
provider's icon, a poster -- and every one of them ends up in an ``href`` or a
``src`` without anybody looking at it. An unofficial community API is not a
reason to expect hostile strings, but it is not a reason to rule them out
either, and the cost of asking is one function.

Worth being exact about what this is and is not for. React 19 already blocks a
``javascript:`` href: its ``sanitizeURL`` rewrites one to a thrown error, and its
regex tolerates control characters interleaved between the letters, so the
obvious dodges are covered. Modern browsers block top-level ``data:``
navigation. So this is not closing a live hole, and pretending otherwise would
be the kind of claim that stops a reader trusting the rest of the comments here.

What it does is stop the property depending on somebody else's internals.
React's own message calls its check "a security precaution" rather than a
guarantee, the API is published to more than this one client, and a value that
reaches the database is a value that also reaches log lines and any future
consumer. Holding the rule here costs a scheme comparison.

Pure: no network, no clock, nothing looked up. It answers about the string.
"""

from urllib.parse import urlparse

#: The only two schemes that name somewhere a browser can be sent. Everything
#: else is either script (``javascript:``), a document inlined into the link
#: itself (``data:``), a local resource (``file:``), or an address for something
#: that is not the web at all (``mailto:``, ``tel:``).
_FOLLOWABLE_SCHEMES = frozenset({"http", "https"})

#: ``https:///path`` and friends: a scheme, then no authority at all. RFC
#: parsing calls the host empty; the browser calls the first path segment the
#: host. See the comment in :func:`is_web_url`.
#:
#: Redundant for this module taken alone -- an empty netloc is refused below
#: anyway, and no test here can tell the difference. It earns its place in the
#: frontend mirror, where ``new URL`` reads ``https:///path`` as host ``path``
#: and would otherwise accept it, and it is written on both sides so the two
#: stay readable as the same rule.
_EMPTY_AUTHORITY = ":///"


def is_web_url(value: str | None) -> bool:
    """Whether ``value`` is an ordinary http(s) address with somewhere to go.

    Deliberately a predicate rather than a sanitiser. A rewritten URL is one
    nobody chose: either it still works, and the rewrite bought nothing, or it
    quietly points somewhere other than intended. The caller drops what this
    refuses, and every place the frontend renders one of these already has a
    state for its absence -- a plain label instead of a link, a placeholder
    instead of a poster.
    """
    if not value:
        return False

    # Checked on the string as it arrived, before parsing, because `urlparse`
    # follows the URL standard in stripping tabs, newlines and leading control
    # characters -- so it reports a perfectly ordinary scheme and host for a
    # value that still contains them. The string is what gets stored and what
    # gets printed into a log line, and neither wants a newline in it.
    if any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ):
        return False

    # Where the two URL standards stop agreeing. `urlparse` implements RFC 3986;
    # the browser that will follow this implements WHATWG, and on a backslash or
    # an empty authority the two do not merely differ in strictness -- they
    # disagree about which host the URL names. Measured:
    #
    #   https://good.test\@evil.test/   RFC: good.test\@evil.test   WHATWG: good.test
    #   https:/\evil.test/              RFC: (no host)              WHATWG: evil.test
    #   https:///path                   RFC: (no host)              WHATWG: path
    #
    # Whatever is decided here, the browser is what navigates, so neither
    # reading can be treated as the truth. Both shapes are refused instead. It
    # costs nothing real -- a URL that genuinely wants a backslash spells it
    # `%5C`, and an empty authority names nowhere under either standard -- and it
    # keeps this check and the browser talking about the same host, which is
    # what a host allowlist would have to rely on.
    if "\\" in value or _EMPTY_AUTHORITY in value:
        return False

    try:
        parsed = urlparse(value)
    except ValueError:
        # A malformed IPv6 host, most plausibly. Unparseable is not followable.
        return False

    # Both halves, and neither is implied by the other. A missing host does not
    # save you from a bad scheme: `javascript://x.test/%0aalert(1)` has a host,
    # and reads to a JavaScript parser as a comment followed by a newline
    # followed by the payload. A good scheme does not save you from a missing
    # host either: an `href` of "https://" is not a link.
    #
    # `.lower()` is belt and braces. `urlparse` already normalises the scheme,
    # so a mutation that removes this changes nothing and no test can tell --
    # it is here so the rule does not silently depend on that staying true.
    return parsed.scheme.lower() in _FOLLOWABLE_SCHEMES and bool(parsed.netloc)


#: Where every poster and provider icon in this app comes from.
#:
#: Not a guess about JustWatch's infrastructure -- it is a constant inside the
#: client library, which builds both fields by concatenating it onto a path out
#: of the API response. ``tests/test_urls.py`` reads the library's own copy and
#: compares, so if they move it, one test says so with a sentence rather than
#: every image quietly failing to load.
#:
#: ``frontend/next.config.ts`` names this same host to Next's image optimiser,
#: and imports it from ``lib/urls.ts`` so there is one spelling of it per side
#: rather than three in total.
CATALOGUE_IMAGE_HOST = "images.justwatch.com"


def is_catalogue_image_url(value: str | None) -> bool:
    """Whether ``value`` is an image this app is willing to make the browser fetch.

    Stricter than :func:`is_web_url`, and deliberately so: an ``href`` waits for
    a click, but a ``src`` is fetched as the page renders, which hands the host
    the viewer's IP address and user agent for nothing. Given what this app is
    about, the request itself is the disclosure.

    The host is affordable to pin because it is not JustWatch's to vary. The
    client library builds these two fields as ``_IMAGES_URL + path``, so any
    other authority means the path did the moving -- and with no separator
    between the two, a field of ``@evil.test/x.jpg`` yields a URL whose host is
    ``evil.test`` and whose scheme and shape are otherwise perfect.

    Compared against the whole authority rather than the parsed hostname, which
    refuses a port and any userinfo in the same comparison. Nothing legitimate
    here carries either, and an authority that is exactly the host is one this
    check and the browser cannot read differently.
    """
    if not is_web_url(value):
        return False
    # `is_web_url` returning True means this parsed once already, so it cannot
    # raise here. Reparsed rather than plumbed through, because a function that
    # returns a bool and a parse result is two functions.
    parsed = urlparse(value)
    # https only. `is_web_url` allows either scheme, since an http link is a
    # link; the optimiser in next.config.ts allows only https, and every real
    # URL from this host is https, so this matches the stricter of the two.
    if parsed.scheme.lower() != "https":
        return False
    # `.lower()` is load-bearing here, unlike on the scheme: `urlparse`
    # normalises the case of `.hostname` but leaves `.netloc` as it arrived.
    return parsed.netloc.lower() == CATALOGUE_IMAGE_HOST
