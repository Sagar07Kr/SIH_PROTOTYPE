let backend = process.env.BACKEND_URL || "http://localhost:8000";
if (!backend.startsWith("http://") && !backend.startsWith("https://")) {
  backend = backend.includes("onrender.com") ? `https://${backend}` : `http://${backend}`;
}
backend = backend.replace(/\/+$/, "");

const nextConfig = {
  reactStrictMode: true,
  // The browser talks to /api/* on its own origin and Next proxies to FastAPI.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
export default nextConfig;
