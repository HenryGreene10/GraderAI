export function isDevBypassAuthEnabled() {
  if (!import.meta.env.DEV) return false;
  const flag = String(import.meta.env.VITE_DEV_BYPASS_AUTH || "").trim();
  if (flag !== "1") return false;
  if (typeof window === "undefined") return false;
  const host = String(window.location.hostname || "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

