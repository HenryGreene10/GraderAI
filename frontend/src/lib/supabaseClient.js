// frontend/src/lib/supabaseClient.js
import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

// TEMP: log to verify values exist (remove after test)
if (!supabaseUrl || !supabaseAnonKey) {
  console.error(
    "Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY. Check frontend/.env and restart the dev server."
  );
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
});

// 👇 add this line (so you can use it from the DevTools console)
if (typeof window !== "undefined") window.supabase = supabase;
// (optional safer dev-only)
// if (import.meta.env.DEV && typeof window !== "undefined") window.supabase = supabase;
