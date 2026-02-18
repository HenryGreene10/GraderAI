import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { supabase } from "../lib/supabaseClient";
import { isDevBypassAuthEnabled } from "../lib/devAuth";

export default function ProtectedRoute({ children }) {
  const location = useLocation();
  const [loading, setLoading] = useState(true);
  const [authed, setAuthed] = useState(false);
  const devBypassEnabled = isDevBypassAuthEnabled();

  if (devBypassEnabled && location.pathname.startsWith("/scan/")) {
    return children;
  }

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setAuthed(!!data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => {
      setAuthed(!!session);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  if (loading) return null;
  return authed ? children : <Navigate to="/auth" replace />;
}
