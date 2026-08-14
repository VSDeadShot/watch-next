/**
 * What the platform in front of this app will carry.
 *
 * Not an app limit. The backend's own cap is `MAX_UPLOAD_BYTES`, it is 32 MB,
 * and it is a real bound on what one import costs in memory and database work.
 * This is a different thing entirely: the ceiling of the road between the
 * browser and that backend.
 *
 * Vercel refuses a request body over 4.5 MB with `413
 * FUNCTION_PAYLOAD_TOO_LARGE`, and it refuses it *before* any code here runs --
 * measured against the deployment, a 4,000 KiB body reached `proxy.ts` and got
 * the 401 it deserved, and a 4,500 KiB body got a 413 without the auth gate
 * being consulted at all. So neither `app/api/[...path]/route.ts` nor the
 * backend's carefully worded 413 ever sees an oversized upload; the browser
 * gets Vercel's plain-text page, which `lib/api.ts` cannot parse into a
 * `detail` and renders as the bare line `413 Request Entity Too Large`.
 *
 * Nothing can be done about that from inside the request, so it is done before
 * it: the import page refuses the file itself and says why. Checking a size in
 * the browser is normally security theatre, but this is not a rule being
 * enforced -- the backend still enforces its own -- it is a person being told
 * the truth about where their file is going to stop.
 */

/** Vercel's request body limit. https://vercel.com/docs/functions/limitations */
export const VERCEL_BODY_LIMIT = 4_500_000;

/**
 * The largest upload this deployment can actually carry, or `null` where the
 * frontend knows of nothing that would stop one.
 *
 * Read on the server, because the answer is a property of the host rather than
 * of the app, and because a `NEXT_PUBLIC_` variable is the one way to get a
 * value into the bundle and the one thing this project does not allow. `VERCEL`
 * is set by the platform during the build and at runtime; unset locally, where
 * `next dev` proxies without a limit and the 32 MB backend cap is the only one
 * there is. Getting that distinction wrong in the other direction would be its
 * own defect: a hard 4.5 MB here would refuse, on a laptop, the very export a
 * person is developing against.
 */
export function uploadLimitBytes(): number | null {
  return process.env.VERCEL ? VERCEL_BODY_LIMIT : null;
}

/** `12_400_000` -> `"12.4 MB"`. Decimal, because that is the unit the limit is quoted in. */
export function megabytes(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(1).replace(/\.0$/, "")} MB`;
}
