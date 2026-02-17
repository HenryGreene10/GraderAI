import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const defaultCertDir = path.resolve(__dirname, ".cert");

function resolveDevPort(defaultPort, env) {
  const portFlagIndex = process.argv.findIndex((arg) => arg === "--port" || arg === "-p");
  if (portFlagIndex !== -1) {
    const value = Number(process.argv[portFlagIndex + 1]);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const envPort = Number(env.VITE_PORT || env.PORT);
  if (Number.isFinite(envPort) && envPort > 0) return envPort;
  return defaultPort;
}

function resolveHttpsConfig(env) {
  const certPath = env.VITE_HTTPS_CERT_FILE || path.join(defaultCertDir, "localhost.pem");
  const keyPath = env.VITE_HTTPS_KEY_FILE || path.join(defaultCertDir, "localhost-key.pem");
  const useHttps = String(env.VITE_DEV_HTTPS || "").toLowerCase() === "true"
    || env.VITE_DEV_HTTPS === "1";
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

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const httpsConfig = resolveHttpsConfig(env);
  const devPort = resolveDevPort(5173, env);
  const apiBaseUrl = String(env.VITE_API_BASE_URL || "").trim().replace(/\/+$/, "");
  if (!apiBaseUrl) {
    throw new Error("VITE_API_BASE_URL is required for frontend dev proxy target");
  }

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    server: {
      host: "0.0.0.0",
      port: devPort,
      https: httpsConfig || false,
      proxy: {
        "/api": {
          target: apiBaseUrl,
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
