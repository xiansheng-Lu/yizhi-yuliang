import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep local milestone builds separate from an already running dev server.
  distDir: process.env.YULIANG_BUILD_DIR || ".next",
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return { beforeFiles: [{ source: "/", destination: "/index.html" }] };
  },
  async redirects() {
    return [
      { source: "/login.html", destination: "/login", permanent: false },
      { source: "/mobile.html", destination: "/mobile", permanent: false },
      { source: "/app.html", destination: "/legacy", permanent: false },
      { source: "/app", destination: "/app/command", permanent: false },
      { source: "/app/campaigns", destination: "/app/plans", permanent: false },
    ];
  },
};
export default nextConfig;
