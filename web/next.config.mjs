/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // The browser talks to the API through this rewrite, so the page works
  // identically whether it is served from Docker or from `npm run dev`.
  async rewrites() {
    const target = process.env.API_BASE || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};
export default nextConfig;
