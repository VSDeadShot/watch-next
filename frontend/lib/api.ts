/**
 * The one way this app talks to the backend.
 *
 * Single helper rather than scattered `fetch` calls, because error handling is
 * the part that gets skipped: every call site would eventually grow its own
 * half-reading of a FastAPI error body, and the ones that did not would show
 * "something went wrong" for a message the backend had already written properly.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** A request that reached the backend and came back a failure. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The backend could not be reached at all -- almost always "it is not running". */
export class ApiUnreachableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiUnreachableError";
  }
}

type ValidationDetail = { loc?: (string | number)[]; msg?: string };

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  // FormData carries its own multipart boundary. Setting a content type here
  // would overwrite it with one that has no boundary in it, and every upload
  // would be rejected as malformed.
  const isForm = init.body instanceof FormData;
  const headers = new Headers(init.headers);
  if (!isForm && init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch {
    // fetch only rejects when the request never completed: the server is down,
    // the port is wrong, CORS refused it. Named separately from an HTTP failure
    // because the fix is different and it is the likeliest thing to go wrong
    // while somebody is running this locally.
    throw new ApiUnreachableError(
      `Could not reach the backend at ${API_BASE_URL}. Is it running?`,
    );
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readError(response));
  }

  // 204 has no body to parse, and DELETE is the route that returns one.
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/**
 * Pull something a person can act on out of a failure.
 *
 * FastAPI answers with `detail`, which is a string for the errors this app
 * raises deliberately and a list of field problems for anything Pydantic
 * rejected. Both are worth showing -- "no title with id 999" and "note: string
 * too long" each say exactly what to do next -- so both are read rather than
 * collapsed into the status code.
 */
async function readError(response: Response): Promise<string> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return `${response.status} ${response.statusText}`.trim();
  }

  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string" && detail) {
    return detail;
  }

  if (Array.isArray(detail)) {
    const problems = (detail as ValidationDetail[])
      .map((item) => {
        // `loc` is ["body", "note"]; the first element names the part of the
        // request rather than the field, so it is dropped.
        const field = item.loc?.slice(1).join(".");
        return field && item.msg ? `${field}: ${item.msg}` : item.msg;
      })
      .filter(Boolean);
    if (problems.length) {
      return problems.join("; ");
    }
  }

  return `${response.status} ${response.statusText}`.trim();
}

/** Whatever went wrong, as one sentence worth putting on a screen. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError || error instanceof ApiUnreachableError) {
    return error.message;
  }
  return "Something went wrong. Try again.";
}
