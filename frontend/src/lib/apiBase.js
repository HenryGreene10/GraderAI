import { supabase } from "./supabaseClient";

export const API_BASE =
  import.meta?.env?.VITE_API_BASE_URL ||
  import.meta?.env?.VITE_BACKEND_BASE ||
  import.meta?.env?.VITE_BACKEND_URL ||
  import.meta?.env?.VITE_API_BASE ||
  "http://localhost:8000";

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
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...headers, ...authHeaders },
  });
}
