/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // The browser talks to /api/* on its own origin and Next proxies to FastAPI.
  // That keeps CORS out of the picture in dev and in the compose stack.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
export default nextConfig;
