import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig(({ mode }) => {
  const isDev = mode === "development";

  return {
    plugins: [vue()],
    server: {
      port: 5173,
      // 开发环境：放宽跨域限制，允许更多来源
      host: true,
      strictPort: false,
      // 开发环境：代理配置，避免 CORS 问题
      proxy: isDev ? {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
          secure: false,
          ws: true,
        },
      } : undefined,
    },
    resolve: {
      alias: {
        "@": resolve(__dirname, "src"),
      },
    },
    // 开发环境：启用 sourcemap 便于调试
    build: {
      sourcemap: isDev,
    },
  };
});