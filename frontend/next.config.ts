import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: the app is client-rendered end to end (every page and
  // component is "use client"), with no server-side data fetching or API
  // routes, so it can ship as plain HTML/JS/CSS served from S3 rather than
  // needing a Node server.
  output: "export",
  // Next's built-in image optimizer needs a running server; static export
  // has none, so images are served as-is instead.
  images: { unoptimized: true },
};

export default nextConfig;
