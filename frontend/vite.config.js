import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const devProxyTarget = String(env.VITE_DEV_PROXY_TARGET || "http://127.0.0.1:8000")
    .trim()
    .replace(/\/+$/, "");

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      https: {
        key: fs.readFileSync(path.resolve(__dirname, ".cert/key.pem")),
        cert: fs.readFileSync(path.resolve(__dirname, ".cert/cert.pem")),
      },
      proxy: {
        "/api": {
          target: devProxyTarget,
          changeOrigin: true,
          secure: false,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./tests/setup.ts"],
    },
  };
});
