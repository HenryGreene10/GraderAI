// frontend/src/components/FileRow.jsx
import React, { useEffect, useRef, useState } from "react";
import { startOCR, pollOCR } from "../lib/ocr";
import { API_BASE } from "../lib/apiBase";
import { supabase } from "../lib/supabaseClient";

export default function FileRow({ file }) {
  const [row, setRow] = useState(() => ({
    ...file,
    ocr_status: file.ocr_status || file.status || "pending",
    text_len: file.text_len || 0,
  }));
  const [text, setText] = useState(file.extracted_text || "");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const startedRef = useRef(false);
  // Minimal OCR panel state
  const [ocrPanelText, setOcrPanelText] = useState("");
  const [ocrPanelStatus, setOcrPanelStatus] = useState("pending");

  // auto-start once for "pending"
  useEffect(() => {
    if (row.ocr_status === "pending" && !startedRef.current) {
      startedRef.current = true;
      (async () => {
        try {
          const resp = await startOCR(file.id);
          // If backend already completes OCR synchronously, reflect immediately
          if (resp && resp.status === "done") {
            setRow((r) => ({ ...r, ocr_status: "done", text_len: Number(resp.text_len || 0) }));
            if (resp.text) setText(resp.text);
            showToast("OCR complete");
            try { console.log("SMOKE_OK", { api: API_BASE, upload: file.id }); } catch {}
          } else {
            setRow((r) => ({ ...r, ocr_status: "processing" }));
          }
        } catch (e) {
          let msg = e?.message || "Failed to start";
          try {
            const parsed = JSON.parse(msg);
            const status = parsed?.status;
            const body = parsed?.body;
            const detail = body?.detail || body?.message || (typeof body === 'string' ? body : "");
            msg = `OCR failed (${status}): ${detail}`;
          } catch {}
          setErr(msg);
          setRow((r) => ({ ...r, ocr_status: "failed" }));
          showToast(msg);
        }
      })();
    }
  }, [row.ocr_status, file.id]);

  // Fetch OCR text for the panel on mount / when upload changes
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await supabase.auth.getUser();
        const ownerId = data?.user?.id;
        const r = await fetch(`${API_BASE}/api/uploads/${file.id}/ocr`, {
          method: "GET",
          headers: ownerId ? { "X-Owner-Id": ownerId, "X-User-Id": ownerId } : {},
        });
        const j = await r.json().catch(() => ({}));
        if (!cancelled) {
          if (j && j.ocr_text) {
            setOcrPanelText(j.ocr_text);
            setOcrPanelStatus(j.status || "done");
          } else {
            setOcrPanelText("");
            setOcrPanelStatus(j?.status || "pending");
          }
        }
      } catch (e) {
        if (!cancelled) {
          setOcrPanelText("");
          setOcrPanelStatus("pending");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [file.id]);

  // poll until done/failed
  useEffect(() => {
    if (row.ocr_status === "processing") {
      const stop = pollOCR(file.id, (s) => {
        setRow((r) => ({ ...r, ocr_status: s.status, text_len: Number(s.text_len || r.text_len || 0) }));
        if (s.extracted_text) setText(s.extracted_text);
        if (s.error) setErr(s.error);
        if (s.status === "done") {
          showToast("OCR complete");
          try { console.log("SMOKE_OK", { api: API_BASE, upload: file.id }); } catch {}
        }
      }, 2000);
      return stop;
    }
  }, [row.ocr_status, file.id]);

  const retry = async () => {
    setErr("");
    setRow((r) => ({ ...r, ocr_status: "pending" }));
    startedRef.current = false;
  };

  async function handleRetry() {
    try {
      setBusy(true);
      setErr("");
      const resp = await startOCR(file.id);
      if (resp && (resp.status === "done" || resp.ocr_status === "done")) {
        setRow((r) => ({ ...r, ocr_status: "done", text_len: Number(resp.text_len || 0) }));
        if (resp.text) setText(resp.text);
        showToast("OCR complete");
        try { console.log("SMOKE_OK", { api: API_BASE, upload: file.id }); } catch {}
      } else {
        setRow((r) => ({ ...r, ocr_status: "processing" }));
      }
    } catch (e) {
      let msg = e?.message || "Failed to start";
      try {
        const parsed = JSON.parse(msg);
        const status = parsed?.status;
        const body = parsed?.body;
        const detail = body?.detail || body?.message || (typeof body === 'string' ? body : "");
        msg = `OCR failed (${status}): ${detail}`;
      } catch {}
      setErr(msg);
      setRow((r) => ({ ...r, ocr_status: "failed" }));
      showToast(msg);
      console.error(e);
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
          <StatusChip value={row.ocr_status} /> {err && <span style={{ color:"#b42318" }}> Â· {err}</span>}
        </div>

        {text && (
          <div style={{ marginTop: 8, whiteSpace: "pre-wrap", fontSize: 14 }}>
            {text}
          </div>
        )}

        {/* Minimal OCR panel */}
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#475467", marginBottom: 4 }}>OCR</div>
          <pre
            style={{
              background: "#f9fafb",
              padding: "8px 10px",
              borderRadius: 6,
              fontSize: 12,
              whiteSpace: "pre-wrap",
              maxHeight: 220,
              overflow: "auto",
              border: "1px solid #e5e7eb",
            }}
          >
            {ocrPanelText || "pending..."}
          </pre>
        </div>

        {(row.ocr_status === "error" || row.ocr_status === "ocr_error" || row.ocr_status === "failed") && !busy && (
          <button onClick={handleRetry} className="btn btn-ghost" aria-label="Retry">
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

function StatusChip({ value }) {
  const colors = {
    pending:  { bg:"#f3f4f6", fg:"#374151", label:"pending" },
    processing:{ bg:"#fef3c7", fg:"#92400e", label:"processing" },
    done:     { bg:"#dcfce7", fg:"#065f46", label:"done" },
    failed:   { bg:"#fee2e2", fg:"#991b1b", label:"failed" },
  }[value] || { bg:"#eef2ff", fg:"#334155", label:value };

  return (
    <span style={{
      background: colors.bg, color: colors.fg, padding:"2px 8px",
      borderRadius:999, fontSize:12, fontWeight:600
    }}>
      {colors.label}
    </span>
  );
}

