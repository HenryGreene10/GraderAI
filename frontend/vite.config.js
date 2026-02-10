import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import os from "os";
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

function isPrivateIpv4(address) {
  if (address.startsWith("10.")) return true;
  if (address.startsWith("192.168.")) return true;
  const match = address.match(/^172\\.(\\d+)\\./);
  if (!match) return false;
  const segment = Number(match[1]);
  return segment >= 16 && segment <= 31;
}

function resolveLanHost() {
  const interfaces = os.networkInterfaces();
  let fallback = "";
  for (const entries of Object.values(interfaces)) {
    for (const net of entries || []) {
      const isIpv4 = net?.family === "IPv4" || net?.family === 4;
      if (!net || !isIpv4 || net.internal) continue;
      if (isPrivateIpv4(net.address)) return net.address;
      if (!fallback) fallback = net.address;
    }
  }
  return fallback;
}

function resolveDevPort(defaultPort) {
  const portFlagIndex = process.argv.findIndex((arg) => arg === "--port" || arg === "-p");
  if (portFlagIndex !== -1) {
    const value = Number(process.argv[portFlagIndex + 1]);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const envPort = Number(process.env.VITE_PORT || process.env.PORT);
  if (Number.isFinite(envPort) && envPort > 0) return envPort;
  return defaultPort;
}

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

export default defineConfig(({ command }) => {
  const httpsConfig = resolveHttpsConfig();
  const devPort = resolveDevPort(5173);
  const publicBaseEnv = String(process.env.VITE_PUBLIC_BASE_URL || "").trim();

  if (command === "serve" && !publicBaseEnv) {
    const lanHost = resolveLanHost();
    if (lanHost) {
      const protocol = httpsConfig ? "https" : "http";
      process.env.VITE_PUBLIC_BASE_URL = `${protocol}://${lanHost}:${devPort}`;
    }
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
  };
});
