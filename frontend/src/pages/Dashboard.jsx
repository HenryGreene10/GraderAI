import { useEffect, useState } from "react";
import { supabase } from "../lib/supabaseClient";

export default function Dashboard() {
  const [email, setEmail] = useState(null);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
  }, []);

  const signOut = async () => {
    await supabase.auth.signOut();
    window.location.href = "/auth";
  };

  return (
    <div style={{ padding: 24 }}>
      <header style={{ display: "flex", justifyContent: "space-between" }}>
        <h2>Dashboard</h2>
        <div>
          <span style={{ marginRight: 12 }}>{email}</span>
          <button onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main style={{ marginTop: 24 }}>
        <div>Classes (coming next)</div>
        <div>Assignments (coming next)</div>
      </main>
    </div>
  );
}
