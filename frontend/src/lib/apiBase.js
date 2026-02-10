import { supabase } from "./supabaseClient";

function normalizeBaseUrl(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) return "";
  return trimmed.replace(/\/+$/, "");
}

export function publicBase() {
  const envBase = normalizeBaseUrl(import.meta.env.VITE_PUBLIC_BASE_URL);
  if (envBase) return envBase;
  return window.location.origin;
}

export function apiBase() {
  return window.location.origin;
}

export async function getAuthHeaders() {
  const { data } = await supabase.auth.getSession();
  const token = data?.session?.access_token;
  if (!token) {
    throw new Error("Missing auth token");
  }
  return { Authorization: `Bearer ${token}` };
}

export async function apiFetch(path, options = {}) {
  const headers = options.headers ? { ...options.headers } : {};
  const authHeaders = await getAuthHeaders();
  return fetch(`${apiBase()}${path}`, {
    ...options,
    headers: { ...headers, ...authHeaders },
  });
}
