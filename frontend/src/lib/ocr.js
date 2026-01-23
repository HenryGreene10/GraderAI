// frontend/src/lib/ocr.js
import { supabase } from "./supabaseClient";
import { API_BASE } from "./apiBase";

/** Fetch current user's id (ownerId) */
async function getOwnerId() {
  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) throw new Error("You must be signed in");
  return data.user.id;
}

async function getAuthHeaders(ownerId) {
  const { data } = await supabase.auth.getSession();
  const token = data?.session?.access_token;
  const headers = {};
  if (ownerId) {
    headers["X-Owner-Id"] = ownerId;
    headers["X-User-Id"] = ownerId;
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/** Start OCR on a specific upload row */
export async function startOCR(uploadId, ownerIdParam) {
  if (!uploadId) throw new Error("uploadId is required");
  const ownerId = ownerIdParam || (await getOwnerId());
  const authHeaders = await getAuthHeaders(ownerId);

  const r = await fetch(`${API_BASE}/api/ocr/start`, {
    method: "POST",
    mode: "cors",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
    },
    body: JSON.stringify({ upload_id: uploadId }),
  });

  if (!r.ok) {
    let body;
    try {
      body = await r.json();
    } catch {
      body = await r.text().catch(() => "");
    }
    throw new Error(JSON.stringify({ status: r.status, body }));
  }
  return r.json();
}

/** One-shot status fetch */
export async function getOCRStatus(uploadId) {
  if (!uploadId) throw new Error("uploadId is required");
  const ownerId = await getOwnerId();
  const authHeaders = await getAuthHeaders(ownerId);

  const r = await fetch(`${API_BASE}/api/ocr/status/${uploadId}`, {
    method: "GET",
    mode: "cors",
    headers: authHeaders,
  });

  if (!r.ok) {
    let body;
    try { body = await r.json(); } catch { body = await r.text().catch(() => ""); }
    throw new Error(JSON.stringify({ status: r.status, body }));
  }
  return r.json();
}

/**
 * Poll status until done/failed.
 * Returns a stop() function to cancel polling.
 */
export function pollOCR(uploadId, onTick, intervalMs = 1500) {
  let stopped = false;

  (async function loop() {
    try {
      const ownerId = await getOwnerId();
      const authHeaders = await getAuthHeaders(ownerId);
      // simple polling loop
      while (!stopped) {
        const r = await fetch(`${API_BASE}/api/ocr/status/${uploadId}`, {
          headers: authHeaders,
          mode: "cors",
        });
        const json = await r.json().catch(() => ({}));
        // Normalize alternative error shape: { state: 'ERROR', message }
        let toEmit = json;
        if (json && typeof json === "object" && !json.status && json.state) {
          const st = String(json.state).toUpperCase();
          if (st === "ERROR") {
            toEmit = { status: "failed", error: json.message || "Error" };
          }
        }
        if (typeof onTick === "function") onTick(toEmit);
        // Only continue polling while "processing"; stop otherwise
        if (toEmit.status !== "processing") break;
        await new Promise((res) => setTimeout(res, intervalMs));
      }
    } catch (e) {
      if (typeof onTick === "function") {
        onTick({ status: "failed", error: String(e?.message || e) });
      }
    }
  })();

  return () => {
    stopped = true;
  };
}
