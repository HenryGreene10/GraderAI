// frontend/src/components/FileRow.jsx
import React, { useEffect, useRef, useState } from "react";
import { startOCR } from "../lib/ocr";
import { API_BASE } from "../lib/apiBase";
import { supabase } from "../lib/supabaseClient";

function FileRow({ file }) {
  const [row, setRow] = useState(() => ({
    ...file,
    ocr_status: file.ocr_status || file.status || "pending",
    text_len: file.text_len || 0,
  }));
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const startedRef = useRef(false);

  // Verdicts / PDF state
  const [verdicts, setVerdicts] = useState(file.verdicts || {});
  const [gradedPath, setGradedPath] = useState(file.graded_pdf_path || null);
  const [gradedSigned, setGradedSigned] = useState(null);

  const initialStatus = (() => {
    if (file.graded_pdf_path) return "pdf_ready";
    const st = String((file.ocr_status || file.status || "").toLowerCase());
    if (st === "processing" || st === "running" || st === "pending") return "processing";
    if (st === "done" || st === "ocr_done") return "ocr_done";
    return "idle";
  })();
  const [status, setStatus] = useState(initialStatus);

  // auto-start once for "pending"
  useEffect(() => {
    if (row.ocr_status === "pending" && !startedRef.current) {
      startedRef.current = true;
      (async () => {
        try {
          const resp = await startOCR(file.id);
          if (resp && (resp.status === "done" || resp.ocr_status === "done")) {
            setRow((r) => ({ ...r, ocr_status: "done", text_len: Number(resp.text_len || 0) }));
            setStatus("ocr_done");
          } else {
            setRow((r) => ({ ...r, ocr_status: "processing" }));
            setStatus("processing");
          }
        } catch (e) {
          setErr(e?.message || "Failed to start");
          setRow((r) => ({ ...r, ocr_status: "failed" }));
          setStatus("error");
        }
      })();
    }
  }, [row.ocr_status, file.id]);

  // Update OCR status only (no text shown)
  const refreshOCR = async () => {
    const { data } = await supabase.auth.getUser();
    const ownerId = data?.user?.id;
    const r = await fetch(`${API_BASE}/api/uploads/${file.id}/ocr`, {
      method: "GET",
      headers: ownerId ? { "X-Owner-Id": ownerId, "X-User-Id": ownerId } : {},
    });
    const j = await r.json().catch(() => ({}));
    const st = String(j?.status || row.ocr_status || "pending").toLowerCase();
    setRow((r) => ({ ...r, ocr_status: st }));
    if (st === "done") setStatus("ocr_done");
    else if (st === "processing" || st === "running" || st === "pending") setStatus("processing");
    else if (st === "failed" || st === "error") setStatus("error");
    else setStatus("idle");
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try { if (!cancelled) await refreshOCR(); } catch {}
    })();
    return () => { cancelled = true; };
  }, [file.id]);

  // Simplified: no long polling here; we refetch status after a short delay

  const retry = async () => {
    setErr("");
    setRow((r) => ({ ...r, ocr_status: "pending" }));
    startedRef.current = false;
  };

  async function handleRunOCR() {
    try {
      setBusy(true);
      setErr("");
      // Prefer new route; fallback to legacy
      let ok = false;
      await fetch(`${API_BASE}/api/ocr/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upload_id: String(file.id) }),
      });
      setRow((r) => ({ ...r, ocr_status: "processing" }));
      setStatus("processing");
      await new Promise((res) => setTimeout(res, 1500));
      await refreshOCR();
      setStatus("ocr_done");
    } catch (e) {
      setErr(e?.message || "Failed to start");
      setRow((r) => ({ ...r, ocr_status: "failed" }));
      setStatus("error");
    } finally {
      setBusy(false);
    }
  }

  // Minimal in-app toast
  const [toast, setToast] = useState("");
  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(""), 2000);
  }

  async function refreshRowFromDB() {
    try {
      const { data: userData } = await supabase.auth.getUser();
      const userId = userData?.user?.id;
      const { data, error } = await supabase
        .from("uploads")
        .select("id,status,ocr_error,graded_pdf_path,verdicts")
        .eq("owner_id", userId)
        .eq("id", file.id)
        .maybeSingle();
      if (error) throw error;
      if (data) {
        setVerdicts(data.verdicts || {});
        setGradedPath(data.graded_pdf_path || null);
        if (data.graded_pdf_path) setStatus("pdf_ready");
      }
    } catch (e) {
      console.error(e);
    }
  }

  // Lightweight prompt-based actions
  const setVerdictsPrompt = async () => {
    try {
      const allowed = new Set(["correct", "incorrect", "partial"]);
      const _q5 = window.prompt("q5: correct/incorrect/partial");
      if (_q5 == null) { showToast("Invalid verdict"); return; }
      const _q6a = window.prompt("q6a: correct/incorrect/partial");
      if (_q6a == null) { showToast("Invalid verdict"); return; }
      const _q6b = window.prompt("q6b: correct/incorrect/partial");
      if (_q6b == null) { showToast("Invalid verdict"); return; }

      const q5 = String(_q5).trim().toLowerCase();
      const q6a = String(_q6a).trim().toLowerCase();
      const q6b = String(_q6b).trim().toLowerCase();
      if (!allowed.has(q5)) { showToast("Invalid verdict"); return; }
      if (!allowed.has(q6a)) { showToast("Invalid verdict"); return; }
      if (!allowed.has(q6b)) { showToast("Invalid verdict"); return; }

      const body = { per_question: { q5, q6a, q6b } };
      const res = await fetch(`${API_BASE}/api/uploads/${file.id}/verdicts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        throw new Error(detail || `Failed: ${res.status}`);
      }
      setVerdicts({ q5, q6a, q6b });
      showToast("Verdicts saved");
      await refreshRowFromDB();
    } catch (e) {
      showToast(e?.message || "Failed to save verdicts");
    }
  };

  const createPdf = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/uploads/${file.id}/pdf`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(String(data?.detail || `Failed: ${res.status}`));
        return;
      }
      setGradedPath(data.path || null);
      const signed = data?.signedUrl
        ? `${data.signedUrl}${String(data.signedUrl).includes("?") ? "&" : "?"}t=${Date.now()}`
        : null;
      setGradedSigned(signed);
      setStatus("pdf_ready");
      showToast("Graded PDF ready");
    } catch (e) {
      showToast(e?.message || "Failed to create PDF");
    }
  };

  const hasVerdicts = verdicts && Object.keys(verdicts).length > 0;

  return (
    <div style={{ display:"flex", gap:12, alignItems:"flex-start", padding:"10px 0", position: "relative" }}>
      {toast && (
        <div style={{
          position: "fixed",
          right: 16,
          bottom: 16,
          background: "rgba(0,0,0,0.85)",
          color: "#fff",
          padding: "8px 12px",
          borderRadius: 8,
          fontSize: 13,
          zIndex: 1000,
          boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
        }}>
          {toast}
        </div>
      )}

      {row.signedUrl ? (
        <img
          src={row.signedUrl}
          alt={row.name}
          style={{ maxHeight: 220, width: "auto", display: "block", objectFit: "cover", borderRadius: 8 }}
          loading="lazy"
        />
      ) : (
        <div style={{ width:160, height:120, background:"#f2f4f7", borderRadius:8 }} />
      )}

      <div style={{ flex:1 }}>
        <div style={{ fontWeight: 600 }}>{row.name}</div>
        <div style={{ fontSize: 12, color: "#667085" }}>
          <StatusChip value={status} /> {err && <span style={{ color:"#b42318" }}> {err}</span>}
        </div>

        {/* Actions */}
        <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn btn-ghost" onClick={handleRunOCR} disabled={busy}>
            Run OCR
          </button>
          <button type="button" className="btn btn-ghost" onClick={setVerdictsPrompt}>
            Set verdicts
          </button>
          <button type="button" className="btn btn-ghost" onClick={createPdf} disabled={!hasVerdicts} title={!hasVerdicts ? "Set verdicts first" : ""}>
            Create graded PDF
          </button>
          {(gradedSigned || gradedPath || row.graded_pdf_path) && (
            <a
              className="btn btn-primary"
              href={gradedSigned || undefined}
              onClick={async (e) => {
                if (gradedSigned) return; // use provided URL
                e.preventDefault();
                try {
                  const key = (gradedPath || row.graded_pdf_path);
                  if (!key) return;
                  const urlRes = await import("../lib/supa").then(m => m.previewUrl("graded-pdfs", key, 3600));
                  if (urlRes.ok && urlRes.url) {
                    window.open(urlRes.url, "_blank");
                  }
                } catch (err) {
                  console.error(err);
                }
              }}
              target={gradedSigned ? "_blank" : undefined}
              rel="noreferrer"
            >
              Download graded PDF
            </a>
          )}
        </div>

        {(status === "error") && !busy && (
          <button onClick={retry} className="btn btn-ghost" aria-label="Retry">
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

function StatusChip({ value }) {
  const colors = {
    idle:       { bg:"#f3f4f6", fg:"#374151", label:"idle" },
    processing: { bg:"#fef3c7", fg:"#92400e", label:"processing" },
    ocr_done:   { bg:"#dcfce7", fg:"#065f46", label:"ocr_done" },
    pdf_ready:  { bg:"#dbeafe", fg:"#1e3a8a", label:"pdf_ready" },
    error:      { bg:"#fee2e2", fg:"#991b1b", label:"error" },
  }[value] || { bg:"#eef2ff", fg:"#334155", label:String(value || "idle") };

  return (
    <span style={{
      background: colors.bg, color: colors.fg, padding:"2px 8px",
      borderRadius:999, fontSize:12, fontWeight:600
    }}>
      {colors.label}
    </span>
  );
}

export default FileRow;
