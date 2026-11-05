import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  transpilePackages: ["@agentproof/contracts"],
  async rewrites() {
    const origin = new URL(
      process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000",
    );
    if (
      !["http:", "https:"].includes(origin.protocol) ||
      origin.username ||
      origin.password ||
      origin.pathname !== "/" ||
      origin.search ||
      origin.hash
    )
      throw new Error(
        "API_INTERNAL_URL must be an HTTP(S) origin without credentials",
      );
    return [
      {
        source: "/api/v1/:path*",
        destination: origin.origin + "/api/v1/:path*",
      },
    ];
  },
  turbopack: {
    rules: { "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" } },
  },
};
export default nextConfig;
