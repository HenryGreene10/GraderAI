// frontend/src/lib/ocr.js
import { supabase } from "./supabaseClient";

const API_BASE = import.meta.env.VITE_BACKEND_URL;

async function authHeader() {
  const { data } = await supabase.auth.getUser();
  const uid = data?.user?.id || "";
  return {
    "Content-Type": "application/json",
    // FastAPI reads this as x_user_id: Header(None)
    "x-user-id": uid,
  };
}

export async function startOCR(uploadId) {
  const res = await fetch(`${API_BASE}/api/ocr/start`, {
    method: "POST",
    headers: await authHeader(),
    body: JSON.stringify({ upload_id: uploadId }),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(txt || `startOCR failed: ${res.status}`);
  }
  return res.json();
}

export async function getOCRStatus(uploadId) {
  const res = await fetch(`${API_BASE}/api/ocr/status/${uploadId}`, {
    headers: await authHeader(),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(txt || `status failed: ${res.status}`);
  }
  return res.json();
}

export function pollOCR(uploadId, onUpdate, intervalMs = 2000) {
  let stopped = false;
  async function tick() {
    if (stopped) return;
    try {
      const s = await getOCRStatus(uploadId);
      onUpdate?.(s);
      if (s.status === "done" || s.status === "failed") return;
    } catch {
      // ignore transient poll errors
    }
    setTimeout(tick, intervalMs);
  }
  tick();
  return () => { stopped = true; };
}
