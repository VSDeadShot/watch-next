import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    // Every poster JustWatch returns is built from this one host by the client
    // library, so the allowlist is exact rather than permissive: a catalogue
    // response cannot turn the image optimiser into a proxy for anything else.
    remotePatterns: [{ protocol: "https", hostname: "images.justwatch.com" }],
  },
};

export default nextConfig;
