import type { NextRequest } from "next/server";

/**
 * Everything the browser sends to the backend goes through here.
 *
 * **Not `proxy.ts` at the project root**, despite this file being what the repo
 * calls "the proxy" everywhere else. That one is Next's name for what used to
 * be middleware, and it gates human access to the whole site. This one forwards
 * API calls and holds the backend's key. Neither can do the other's job: this
 * handler authenticates *to* the backend and nothing *to* it, which is exactly
 * why the root file had to exist.
 *
 * The backend has one user and no login, so deployed it needs a secret in
 * front of it -- and this app renders in the browser, which means any value the
 * frontend knows is in the bundle and public. `NEXT_PUBLIC_` says so in its own
 * name. So the secret lives on the server, this handler holds it, and the page
 * never sees it.
 *
 * Two things fall out of that which are worth having on their own. The API's
 * address stops being compiled into the client bundle, so moving the backend no
 * longer needs a frontend rebuild. And the browser only ever talks to its own
 * origin, so there is no CORS in the deployed path at all.
 *
 * **This runs locally too, with no bypass.** `API_BASE_URL` falls back to the
 * same localhost the app has always used and the secret is simply unset, which
 * the backend reads as "no gate". One code path, so this cannot be the thing
 * that worked on a laptop and failed in deployment.
 */

// Node rather than Edge: this streams request bodies straight through, and
// `duplex: "half"` is a Node fetch affordance.
export const runtime = "nodejs";
// A proxy that cached would serve one person's history to the next request.
export const dynamic = "force-dynamic";

const BACKEND = process.env.API_BASE_URL ?? "http://localhost:8000";
const SECRET = process.env.WATCH_NEXT_API_SECRET;

type Params = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, { params }: Params) {
  const { path } = await params;

  const target = new URL(`/api/${path.join("/")}`, BACKEND);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  // Forwarded rather than rebuilt: a multipart upload carries its boundary in
  // this header, and inventing a new one makes every import unparseable.
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  if (SECRET) headers.set("X-API-Key", SECRET);

  const body = request.body;

  let response: Response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers,
      body,
      // Streamed rather than buffered, so an import is not read into this
      // function's memory on its way past.
      ...(body ? { duplex: "half" } : {}),
      cache: "no-store",
    } as RequestInit);
  } catch {
    // `lib/api.ts` separates "the server answered with a failure" from "there
    // was no server", because the fix differs and the second is what somebody
    // running this locally hits most. Through a proxy that distinction would
    // otherwise be lost -- a dead backend would arrive as an ordinary 502 from
    // Vercel. The header carries it across; nothing else uses this status.
    return Response.json(
      { detail: `Could not reach the backend at ${BACKEND}. Is it running?` },
      { status: 502, headers: { "x-proxy-unreachable": "1" } },
    );
  }

  // Only the content type is passed back. Copying the upstream headers
  // wholesale would forward its `content-length` alongside a body the runtime
  // may re-encode, and hop-by-hop headers that are not ours to relay.
  const out = new Headers();
  const upstreamType = response.headers.get("content-type");
  if (upstreamType) out.set("content-type", upstreamType);

  // 204 is the watchlist delete, and a 204 carrying a body is a protocol error
  // rather than a stylistic one.
  return new Response(response.status === 204 ? null : response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: out,
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
