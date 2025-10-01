// frontend/src/lib/supa.js
import { createClient } from "@supabase/supabase-js";

// Supabase client (explicit env detection)
const SUPA_URL = String(import.meta.env?.VITE_SUPABASE_URL || "").trim();
const SUPA_KEY = String(import.meta.env?.VITE_SUPABASE_ANON_KEY || "").trim();
console.info("[supa] env present:", { url: !!SUPA_URL, key: !!SUPA_KEY });

let supa;
if (SUPA_URL && SUPA_KEY) {
  try {
    supa = createClient(SUPA_URL, SUPA_KEY);
  } catch (_e) {
    // fall through to stub
  }
}

if (!supa) {
  // minimal stub so app renders even without env
  const auth = {
    async getSession() { return { data: { session: null }, error: null }; },
    async getUser() { return { data: { user: null }, error: null }; },
    onAuthStateChange(cb) {
      const subscription = { unsubscribe() {} };
      try { if (typeof cb === "function") setTimeout(() => cb("INITIAL", null), 0); } catch {}
      return { data: { subscription } };
    },
    async signOut() { return { error: null }; },
    async signInWithPassword() { return { data: null, error: { message: "Missing Supabase env" } }; },
    async signUp() { return { data: null, error: { message: "Missing Supabase env" } }; },
  };
  const chain = () => ({
    select() { return Promise.resolve({ data: [], error: null }); },
    insert() { return Promise.resolve({ data: null, error: null }); },
    update() { return Promise.resolve({ data: null, error: null }); },
    delete() { return Promise.resolve({ data: null, error: null }); },
    eq() { return this; },
    is() { return this; },
    order() { return this; },
  });
  supa = {
    auth,
    from() { return chain(); },
    storage: {
      from() {
        return {
          getPublicUrl() { return { data: { publicUrl: null } }; },
          async createSignedUrl() { return { data: { signedUrl: "" }, error: null }; },
          async remove() { return { data: null, error: null }; },
        };
      },
    },
  };
}


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
