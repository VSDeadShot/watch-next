import { NextResponse, type NextRequest } from "next/server";

/**
 * The gate in front of the whole site.
 *
 * **Not to be confused with `app/api/[...path]/route.ts`**, which this repo's
 * prose also calls "the proxy". That one forwards API calls to the backend and
 * attaches the backend's key. This one decides whether the request gets to
 * exist at all. The collision is Next's: `proxy.ts` is the framework's name for
 * what used to be `middleware.ts`, and it is not negotiable.
 *
 * ## Why this exists
 *
 * Vercel's deployment protection was on and the site was still public. Standard
 * Protection on the Hobby plan covers preview URLs and immutable deployment
 * URLs but deliberately exempts the production domain -- which is the only URL
 * anybody actually uses. Covering it needs the paid Advanced Deployment
 * Protection add-on. Verified the hard way on 2026-08-11: with SSO reporting
 * `enabled: true`, `GET https://watch-next-tau.vercel.app/api/stats` returned
 * 200 and 1,754 bytes of real viewing history to an unauthenticated request,
 * while the branch alias and the deployment URL both redirected to a login.
 *
 * The API's own secret cannot help there. `app/api/[...path]/route.ts` has no
 * authentication of its own and attaches `X-API-Key` to everything it forwards,
 * so a reachable frontend is a reachable backend by construction. That is the
 * whole design -- the browser must never hold the key -- and it means human
 * access has to be gated somewhere the browser reaches first. Here.
 *
 * ## Why Basic auth rather than a login page
 *
 * A session cookie would want a seventh route, a form, cookie signing, an
 * expiry and a CSRF story. This app has six routes on purpose and a nav already
 * at its measured 320px ceiling. Basic auth adds no route, no component, no
 * client JavaScript and no state, and it still works from `curl -u`, which is
 * how this API gets poked by hand. The price is the browser's own unstyled
 * prompt, once per session, before anything renders -- cheaper than a login
 * screen that would have to be designed and then maintained.
 *
 * There is no rate limiting here. It would need state shared across serverless
 * invocations, and for one user with a long random password over HTTPS behind
 * Vercel's own DDoS protection it costs more than it buys. That does put the
 * whole weight on the password being long and random.
 */

// Not `middleware.ts`: Next 16 renamed the convention, still accepts the old
// name with a deprecation warning, and hard-errors if both files exist.
// The exported function must be named `proxy` -- Next throws
// ProxyMissingExportError otherwise, which is at least a loud way to be wrong.

const USER = process.env.WATCH_NEXT_USER;
const PASSWORD = process.env.WATCH_NEXT_PASSWORD;

// `next dev` only. Not a general "is this production" test -- it is the one
// signal that positively means "somebody is running this on their own machine",
// which is the only place an ungated site is the right default.
const IS_DEV = process.env.NODE_ENV === "development";

// The same word the backend uses for the same decision, so the two halves of
// this app do not have two vocabularies for "yes, I know it is open".
const WAIVED = process.env.ALLOW_UNAUTHENTICATED === "true";

// Blank and whitespace-only both mean "not set", matching app/api/security.py.
// A variable holding spaces is one somebody meant to fill in, and treating it
// as configured would gate the site behind a password nobody can type.
const CONFIGURED = Boolean(USER?.trim() && PASSWORD?.trim());

export async function proxy(request: NextRequest) {
  if (IS_DEV || WAIVED) {
    return NextResponse.next();
  }

  if (!CONFIGURED) {
    // Refuses to serve rather than serving openly, for the reason the backend
    // refuses to boot: a deploy that lost its environment variables must not
    // come up looking exactly like a working one. 503 rather than 500 -- this
    // is a configuration state, not a crash, and it says nothing about what is
    // behind it.
    return new NextResponse(
      "This deployment is not configured. WATCH_NEXT_USER and " +
        "WATCH_NEXT_PASSWORD must be set.\n",
      { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  const offered = read(request.headers.get("authorization"));
  if (offered !== null && (await sameCredential(offered, `${USER}:${PASSWORD}`))) {
    return NextResponse.next();
  }

  return challenge();
}

/** The credential a Basic header carries, or null if it carries none. */
function read(header: string | null): string | null {
  if (!header?.startsWith("Basic ")) {
    return null;
  }
  try {
    // Decoded but not split. A password is allowed to contain a colon and the
    // username is not, so the pair is compared whole -- which also means the
    // refusal cannot say which half was wrong.
    return atob(header.slice("Basic ".length));
  } catch {
    // Not valid base64. A malformed header is a failed attempt, not a 400:
    // answering differently would tell a caller how far it got.
    return null;
  }
}

async function sameCredential(offered: string, expected: string): Promise<boolean> {
  // `crypto.timingSafeEqual` is a Node API and this may run on Edge, so both
  // sides are hashed and the digests compared instead. Digests are a fixed 32
  // bytes, so the comparison leaks neither the length nor the content of what
  // was expected.
  const [a, b] = await Promise.all([digest(offered), digest(expected)]);
  let difference = 0;
  for (let i = 0; i < a.length; i++) {
    difference |= a[i] ^ b[i];
  }
  return difference === 0;
}

async function digest(value: string): Promise<Uint8Array> {
  return new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
  );
}

function challenge(): NextResponse {
  return new NextResponse("Authentication required.\n", {
    status: 401,
    headers: {
      // The realm is the one part of the browser's prompt this app controls.
      "WWW-Authenticate": 'Basic realm="watch next", charset="UTF-8"',
      "content-type": "text/plain; charset=utf-8",
    },
  });
}

// No `config.matcher` on purpose. Every path is gated, including `_next`
// assets: the browser sends the credential with subresources once it has one,
// so the only cost is on the first request, and an exclusion list is a place
// for something to be public by accident. The pages matter as much as the API
// here -- a public shell that renders an error still announces what this is and
// who it belongs to.
