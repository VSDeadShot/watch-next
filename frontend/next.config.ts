import type { NextConfig } from "next";

import { CATALOGUE_IMAGE_HOST } from "./lib/urls";

const nextConfig: NextConfig = {
  images: {
    // Every poster JustWatch returns is built from this one host by the client
    // library, so the allowlist is exact rather than permissive: a catalogue
    // response cannot turn the image optimiser into a proxy for anything else.
    //
    // Imported rather than written out, because `lib/urls.ts` holds the same
    // host for the checks in the components -- including the provider icon,
    // which is a plain `<img>` and never reaches this optimiser at all. Two
    // spellings of one hostname is one that gets updated and one that does not.
    remotePatterns: [{ protocol: "https", hostname: CATALOGUE_IMAGE_HOST }],
  },
};

export default nextConfig;
