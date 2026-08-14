import ImportPanel from "@/components/ImportPanel";
import { uploadLimitBytes } from "@/lib/limits";

/**
 * The one route in this app that is a server component wrapping a client one.
 *
 * Everything on the page is interactive and lives in `ImportPanel`. This exists
 * only to read `uploadLimitBytes()`, which is a property of the host rather
 * than of the app and so cannot be read in the browser: the sole way to put a
 * build-time value into the bundle is a `NEXT_PUBLIC_` variable, and this
 * project has none and allows none. Read here, passed down as a number, and the
 * page stays statically rendered either way.
 */
export default function ImportPage() {
  return <ImportPanel limit={uploadLimitBytes()} />;
}
