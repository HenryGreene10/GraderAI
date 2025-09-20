// frontend/src/lib/supa.js
import { createClient } from "@supabase/supabase-js";

// Supabase client
export const supa = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
);

// Helpers per request
const norm = (p) => (!p ? p : String(p).replace(/^\/+/, "").replace(/\/{2,}/g, "/"));
const strip = (p, bucket = "submissions") =>
  !p ? p : String(p).startsWith(`${bucket}/`) ? String(p).slice(bucket.length + 1) : norm(p);

// Preview URL helper (public if available, else signed)
export async function previewUrl(bucket, storagePath, expires = 300) {
  const path = strip(storagePath, bucket);
  const pub = supa.storage.from(bucket).getPublicUrl(path);
  if (pub?.data?.publicUrl) return { ok: true, url: pub.data.publicUrl };
  const { data, error } = await supa.storage.from(bucket).createSignedUrl(path, expires);
  if (error) return { ok: false, error: error.message };
  return { ok: true, url: data?.signedUrl };
}

// Back-compat: keep older helpers if other modules still import them
export function normalizePath(path) {
  return norm(path);
}

export async function getObjectURL(bucket, path, opts) {
  const res = await previewUrl(bucket, path, opts?.expiresIn ?? 300);
  return res;
}

export async function removeObjects(bucket, paths) {
  try {
    const keys = (paths || []).map((p) => strip(p, bucket));
    const { data, error } = await supa.storage.from(bucket).remove(keys);
    if (error) return { ok: false, data, error: String(error?.message || error) };
    return { ok: true, data };
  } catch (e) {
    return { ok: false, data: null, error: String(e?.message || e) };
  }
}

export { norm, strip };
export default supa;
