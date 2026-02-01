import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const defaultCertDir = path.resolve(__dirname, ".cert");
const certPath =
  process.env.VITE_HTTPS_CERT_FILE || path.join(defaultCertDir, "localhost.pem");
const keyPath =
  process.env.VITE_HTTPS_KEY_FILE || path.join(defaultCertDir, "localhost-key.pem");
const useHttps = String(process.env.VITE_DEV_HTTPS || "").toLowerCase() === "true"
  || process.env.VITE_DEV_HTTPS === "1";

function resolveHttpsConfig() {
  if (!useHttps) return false;
  const hasCert = fs.existsSync(certPath);
  const hasKey = fs.existsSync(keyPath);
  if (!hasCert || !hasKey) {
    console.warn(
      `[dev:https] HTTPS cert/key not found. Generate with mkcert:\\n` +
        `  mkcert -install\\n` +
        `  mkcert -key-file ${keyPath} -cert-file ${certPath} localhost 127.0.0.1 ::1 <LAN_IP>`
    );
    return false;
  }
  return {
    cert: fs.readFileSync(certPath),
    key: fs.readFileSync(keyPath),
  };
}

const httpsConfig = resolveHttpsConfig();

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    https: httpsConfig || false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
  },
});
