// 前端的 /api 轉給後端(uvicorn factorymind.api:app),瀏覽器不需要知道後端位址。
const backend = process.env.FACTORYMIND_API ?? "http://127.0.0.1:8000";

/** @type {import("next").NextConfig} */
export default {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
