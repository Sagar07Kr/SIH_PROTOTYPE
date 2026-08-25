let backend = process.env.BACKEND_URL;
if (!backend || backend.includes("localhost") || backend.includes("127.0.0.1")) {
  backend = process.env.NODE_ENV === "production"
    ? "https://layoutloom-backend-9kpx.onrender.com"
    : "http://localhost:8000";
}
if (!backend.startsWith("http://") && !backend.startsWith("https://")) {
  backend = backend.includes("onrender.com") ? `https://${backend}` : `http://${backend}`;
}
backend = backend.replace(/\/+$/, "");

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
export default nextConfig;
