import React, { useState, useMemo } from "react";

/**
 * VerdictsDialog
 * Props: { open, onClose, onSave, initialVerdicts }
 * - Renders three radio groups (q5, q6a, q6b) for values: correct | incorrect | partial
 * - Local state; Cancel/Save buttons; no UI library
 * - On Save: calls onSave(verdicts) then onClose()
 */
export default function VerdictsDialog({ open, onClose, onSave, initialVerdicts, initial }) {
  const init = useMemo(() => (initialVerdicts || initial || {}), [initialVerdicts, initial]);
  const [vals, setVals] = useState({
    q5: init.q5 || "partial",
    q6a: init.q6a || "partial",
    q6b: init.q6b || "partial",
  });
  const [saving, setSaving] = useState(false);

  if (!open) return null;

  const setField = (k, v) => setVals((s) => ({ ...s, [k]: v }));
  const opts = ["correct", "incorrect", "partial"];

  async function save() {
    setSaving(true);
    try {
      await onSave?.({ ...vals });
    } finally {
      setSaving(false);
      onClose?.();
    }
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
      <div style={{ background: "#fff", borderRadius: 10, padding: 16, width: 360, boxShadow: "0 6px 24px rgba(0,0,0,0.2)" }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Set verdicts</div>

        {(["q5", "q6a", "q6b"]).map((k) => (
          <div key={k} style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#475467", marginBottom: 4 }}>{k.toUpperCase()}</div>
            <div style={{ display: "flex", gap: 10 }}>
              {opts.map((o) => (
                <label key={o} style={{ fontSize: 13 }}>
                  <input
                    type="radio"
                    name={`v-${k}`}
                    value={o}
                    checked={vals[k] === o}
                    onChange={(e) => setField(k, e.target.value)}
                  /> {o}
                </label>
              ))}
            </div>
          </div>
        ))}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>Save</button>
        </div>
      </div>
    </div>
  );
}
