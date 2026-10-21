import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
      { source: "/app", destination: "/app/overview", permanent: false },
      { source: "/app/campaigns", destination: "/app/plans", permanent: false },
    ];
  },
};
export default nextConfig;
