import { useState } from "react";
import { supabase } from "../lib/supabaseClient";

export default function AuthPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");

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

  return (
    <div style={{ maxWidth: 420, margin: "64px auto" }}>
      <h1>Teacher Login</h1>
      <form style={{ display: "grid", gap: 8 }}>
        <input placeholder="School email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={onSignIn}>Sign In</button>
          <button type="button" onClick={onSignUp}>Sign Up</button>
        </div>
        <div style={{ color: "#666" }}>{msg}</div>
      </form>
    </div>
  );
}
