/**
 * Whether a URL from the API is one this app will put in an `href`.
 *
 * **This mirrors `app/core/urls.py` by hand, the way `types.ts` mirrors the
 * Pydantic schemas. Change one, change the other.**
 *
 * The backend already drops an unusable link twice over -- once as it arrives
 * from JustWatch, once on the way back out -- so in a healthy deployment this
 * never fires. It is here for the two cases that outlive a backend rule: rows
 * cached before the rule existed, and the plain fact that a client rendering
 * somebody else's strings into the DOM should not have to assume the server
 * got it right.
 *
 * Worth being straight about the size of this. React 19 already rewrites a
 * `javascript:` href into a thrown error, and its regex tolerates control
 * characters interleaved between the letters, so the obvious dodges are
 * covered; browsers block top-level `data:` navigation on their own. Nothing
 * here is closing a live hole. What it does is stop the property depending on
 * a framework internal that React's own message calls "a security precaution"
 * rather than a guarantee.
 */

// The only two schemes that name somewhere a browser can be sent. `URL`
// lowercases the protocol, so a mixed-case attempt lands here as itself.
const FOLLOWABLE = new Set(["http:", "https:"]);

// Whitespace and the C0/DEL controls, by code point rather than by a character
// class holding the characters themselves -- a regex literal with a raw NUL in
// it is a thing editors and diffs mangle silently.
//
// Checked on the string as it arrived, because `URL` follows the standard in
// stripping tabs and newlines: it reports an ordinary protocol and host for a
// value that still contains them.
function hasControlOrSpace(value: string): boolean {
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0;
    if (code <= 0x20 || code === 0x7f || /\s/.test(character)) return true;
  }
  return false;
}

/**
 * The URL if it can be followed, otherwise null.
 *
 * Returns the original string rather than a normalized one. A rewritten URL is
 * one nobody chose: either it still works and the rewrite bought nothing, or it
 * quietly points somewhere else. Callers already have a state for null -- a
 * plain label instead of a link.
 */
export function webUrl(value: string | null | undefined): string | null {
  if (!value || hasControlOrSpace(value)) return null;

  // Where the two URL standards stop agreeing. The backend's `urlparse`
  // implements RFC 3986 and `new URL` here implements WHATWG, and on a
  // backslash or an empty authority they disagree about which host the URL
  // names -- `https://good.test\@evil.test/` is host `good.test\@evil.test` to
  // one and `good.test` to the other. Both shapes are refused on both sides so
  // that the mirror stays a mirror. A URL that genuinely wants a backslash
  // spells it `%5C`.
  if (value.includes("\\") || value.includes(":///")) return null;

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    // Relative, protocol-relative, or not a URL at all. `new URL` needs a base
    // for those, and deliberately is not given one: a link that inherits this
    // app's origin is not the third-party destination an offer is supposed to
    // be, and quietly resolving it against the current page would hide that.
    return null;
  }

  // The host as well as the scheme. "https://" parses and names nowhere.
  return FOLLOWABLE.has(parsed.protocol) && parsed.host ? value : null;
}

/**
 * The one host every poster and provider icon comes from.
 *
 * Exported because `next.config.ts` needs the same string for the image
 * optimiser's `remotePatterns`, and a hostname spelled twice on one side is a
 * hostname that will be changed once. `app/core/urls.py` holds the third copy,
 * unavoidably, and its comment says so.
 */
export const CATALOGUE_IMAGE_HOST = "images.justwatch.com";

/**
 * An image URL if it is one this app will let the browser fetch, otherwise null.
 *
 * Stricter than `webUrl`, because an `href` and a `src` are not the same risk.
 * A link waits for a click; an image is requested as the page draws, so a
 * hostile host is handed the viewer's IP address and user agent with no action
 * on their part at all. Given that this app's whole subject is what somebody
 * watches, the request is itself the disclosure.
 *
 * Pinning the host is affordable because it is not really JustWatch's to vary:
 * the client library builds both fields by concatenating its own constant onto
 * a path from the response, so any other authority means the path moved it --
 * and with nothing between the two, a path of `@evil.test/x.jpg` does exactly
 * that while leaving a URL that otherwise looks perfectly ordinary.
 *
 * The two posters go through `next/image`, whose optimiser already refuses an
 * off-list host with a 400 before making any request, so for those this is the
 * second of two checks. The provider icon is a deliberate plain `<img>` -- a
 * fixed 32px logo has nothing for the optimiser to save -- and for that one
 * this is the only check there is.
 */
export function imageUrl(value: string | null | undefined): string | null {
  if (!value || !webUrl(value)) return null;

  // Reparsed rather than threaded out of `webUrl`, which returns the string it
  // was given: a function that answers one question is worth more than one that
  // hands back its working.
  const parsed = new URL(value);

  // `host` is the host the browser will actually connect to: userinfo resolved
  // away, a default port dropped, an IDNA hostname already folded. That makes
  // this the check that matches what happens, which is why it is the one used
  // here rather than a comparison against the raw authority.
  //
  // It also makes this side very slightly more permissive than the backend, and
  // running both over the same table -- 62 cases -- found exactly where:
  //
  //     https://images.justwatch.com:443/x.jpg        backend no, here yes
  //     https://user:pw@images.justwatch.com/x.jpg    backend no, here yes
  //
  // Both of those genuinely do fetch from the allowlisted host, so neither is a
  // hole; `urlparse` compares the authority as a string and so keeps the port
  // and the userinfo, which is stricter than it needs to be rather than wrong.
  // The divergence only ever runs this way, and the backend refuses both on the
  // way in and on the way out, so nothing shaped like this reaches a browser
  // through this app's API at all.
  return parsed.protocol === "https:" && parsed.host === CATALOGUE_IMAGE_HOST
    ? value
    : null;
}
