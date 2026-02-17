import { useEffect, useState } from "react";
import { supabase } from "../lib/supabaseClient";

export default function AuthPage() {
  const [email, setEmail] = useState("");
  const [magicEmail, setMagicEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");
  const [currentUserEmail, setCurrentUserEmail] = useState(null);

  useEffect(() => {
    let active = true;
    supabase.auth.getUser().then(({ data }) => {
      if (!active) return;
      setCurrentUserEmail(data?.user?.email || null);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!active) return;
      setCurrentUserEmail(session?.user?.email || null);
    });
    return () => {
      active = false;
      sub?.subscription?.unsubscribe?.();
    };
  }, []);

  const onSignIn = async (e) => {
    e.preventDefault();
    setMsg("Signing in…");
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setMsg(error.message);
    else window.location.href = "/";
  };

  const onSignUp = async (e) => {
    e.preventDefault();
    setMsg("Creating account…");
    const { error } = await supabase.auth.signUp({ email, password });
    if (error) setMsg(error.message);
    else setMsg("Account created. If confirmations are OFF, click Sign In.");
  };

  const onMagicLink = async (e) => {
    e.preventDefault();
    const targetEmail = String(magicEmail || email || "").trim();
    if (!targetEmail) {
      setMsg("Enter your email to receive a magic link.");
      return;
    }
    setMsg("Sending magic link…");
    const { error } = await supabase.auth.signInWithOtp({
      email: targetEmail,
      options: {
        emailRedirectTo: `${window.location.origin}/`,
        shouldCreateUser: false,
      },
    });
    if (error) {
      setMsg(error.message);
      return;
    }
    setMsg(`Magic link sent to ${targetEmail}. Open that email on this same device/browser.`);
  };

  return (
    <div style={{ maxWidth: 420, margin: "64px auto" }}>
      <h1>Teacher Login</h1>
      <p style={{ color: "#666", marginTop: 0 }}>
        Current user (`supabase.auth.getUser()`): {currentUserEmail || "none"}
      </p>
      <form style={{ display: "grid", gap: 8 }}>
        <input placeholder="School email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={onSignIn}>Sign In</button>
          <button type="button" onClick={onSignUp}>Sign Up</button>
        </div>
        {import.meta.env.DEV && (
          <>
            <div style={{ borderTop: "1px solid #eee", paddingTop: 8, marginTop: 4 }}>
              <div style={{ fontWeight: 600 }}>DEV login shortcut</div>
              <input
                placeholder="Email for magic link"
                value={magicEmail}
                onChange={(e) => setMagicEmail(e.target.value)}
              />
              <button type="button" onClick={onMagicLink}>
                Send me a magic link
              </button>
            </div>
          </>
        )}
        <div style={{ color: "#666" }}>{msg}</div>
      </form>
    </div>
  );
}
