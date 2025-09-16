// frontend/src/components/FileRow.jsx
import React, { useEffect, useRef, useState } from "react";
import { startOCR, pollOCR } from "../lib/ocr";

export default function FileRow({ file }) {
  const [status, setStatus] = useState(file.status || "pending");
  const [text, setText] = useState(file.extracted_text || "");
  const [err, setErr] = useState("");
  const startedRef = useRef(false);

  // auto-start once for "pending"
  useEffect(() => {
    if (status === "pending" && !startedRef.current) {
      startedRef.current = true;
      (async () => {
        try {
          await startOCR(file.id);
          setStatus("processing");
        } catch (e) {
          setErr(e.message || "Failed to start");
          setStatus("failed");
        }
      })();
    }
  }, [status, file.id]);

  // poll until done/failed
  useEffect(() => {
    if (status === "processing" || status === "pending") {
      const stop = pollOCR(file.id, (s) => {
        setStatus(s.status);
        if (s.extracted_text) setText(s.extracted_text);
        if (s.error) setErr(s.error);
      }, 2000);
      return stop;
    }
  }, [status, file.id]);

  const retry = async () => {
    setErr("");
    setStatus("pending");
    startedRef.current = false;
  };

  return (
    <div style={{ display:"flex", gap:12, alignItems:"flex-start", padding:"10px 0" }}>
      {file.signedUrl ? (
        <img
          src={file.signedUrl}
          alt={file.name}
          style={{ maxHeight: 220, width: "auto", display: "block", objectFit: "cover", borderRadius: 8 }}
          loading="lazy"
        />
      ) : (
        <div style={{ width:160, height:120, background:"#f2f4f7", borderRadius:8 }} />
      )}

      <div style={{ flex:1 }}>
        <div style={{ fontWeight: 600 }}>{file.name}</div>
        <div style={{ fontSize: 12, color: "#667085" }}>
          <StatusChip value={status} /> {err && <span style={{ color:"#b42318" }}> · {err}</span>}
        </div>

        {text && (
          <div style={{ marginTop: 8, whiteSpace: "pre-wrap", fontSize: 14 }}>
            {text}
          </div>
        )}

        {status === "failed" && (
          <button onClick={retry} className="btn btn-primary" style={{ marginTop: 8 }}>
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

function StatusChip({ value }) {
  const colors = {
    pending:  { bg:"#fef3c7", fg:"#92400e", label:"pending" },
    processing:{ bg:"#e0e7ff", fg:"#3730a3", label:"processing" },
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
