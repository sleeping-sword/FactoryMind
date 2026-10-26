// 前端的 /api 轉給後端(uvicorn factorymind.api:app),瀏覽器不需要知道後端位址。
const backend = process.env.FACTORYMIND_API ?? "http://127.0.0.1:8000";

/** @type {import("next").NextConfig} */
export default {
  // 問 AI 可能要重試好幾輪才回來;預設 30 秒會先把連線切掉,畫面顯示 500
  experimental: { proxyTimeout: 180_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
