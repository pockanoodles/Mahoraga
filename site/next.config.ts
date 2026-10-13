import type { NextConfig } from "next";

// The landing page lives at kaitosoeno.com/mahoraga, served by its own Worker
// on that route (wrangler.jsonc), so every URL it makes starts with /mahoraga.
const nextConfig: NextConfig = {
  output: "export",
  basePath: "/mahoraga",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
