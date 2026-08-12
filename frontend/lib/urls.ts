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
