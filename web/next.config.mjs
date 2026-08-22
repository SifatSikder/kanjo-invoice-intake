/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // No rewrites: Next bakes those into the build manifest, so they cannot read
  // API_BASE from the runtime environment. app/api/[...path]/route.ts proxies
  // to the pipeline API per request instead.
};
export default nextConfig;
