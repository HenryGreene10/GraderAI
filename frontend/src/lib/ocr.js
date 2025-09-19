// frontend/src/lib/ocr.js
import { supabase } from "./supabaseClient";

const API_BASE = import.meta.env.VITE_BACKEND_URL; // e.g. https://graderai-hs7d.onrender.com
if (!API_BASE) console.error("VITE_BACKEND_URL is missing");

/** Fetch current user's id (ownerId) */
async function getOwnerId() {
  const { data, error } = await supabase.auth.getUser();
  if (error || !data?.user) throw new Error("You must be signed in");
  return data.user.id;
}

/** Start OCR on a specific upload row */
export async function startOCR(uploadId) {
  if (!uploadId) throw new Error("uploadId is required");
  const ownerId = await getOwnerId();

  const r = await fetch(`${API_BASE}/api/ocr/start`, {
    method: "POST",
    mode: "cors",
    headers: {
      "Content-Type": "application/json",
      // Send BOTH to satisfy either backend variant
      "X-Owner-Id": ownerId,
      "X-User-Id": ownerId,
    },
    body: JSON.stringify({ upload_id: uploadId }),
  });

  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || `startOCR failed (${r.status})`);
  }
  return r.json();
}

/** One-shot status fetch */
export async function getOCRStatus(uploadId) {
  if (!uploadId) throw new Error("uploadId is required");
  const ownerId = await getOwnerId();

  const r = await fetch(`${API_BASE}/api/ocr/status/${uploadId}`, {
    method: "GET",
    mode: "cors",
    headers: {
      "X-Owner-Id": ownerId,
      "X-User-Id": ownerId,
    },
  });

  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || `status failed (${r.status})`);
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
      // simple polling loop
      while (!stopped) {
        const r = await fetch(`${API_BASE}/api/ocr/status/${uploadId}`, {
          headers: {
            "X-Owner-Id": ownerId,
            "X-User-Id": ownerId,
          },
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

        if (toEmit.status === "done" || toEmit.status === "failed") break;
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
