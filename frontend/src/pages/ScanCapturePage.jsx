import React, { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";
import { apiBase } from "../lib/apiBase";

function scanLabel(mode) {
  if (mode === "master_key") return "Master key";
  if (mode === "student") return "Student scan";
  return "Scan";
}

export default function ScanCapturePage() {
  const { token } = useParams();
  const fileRef = useRef(null);
  const { toast } = useToast();
  const [mode, setMode] = useState("");
  const [status, setStatus] = useState("pending");
  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!token) return;
    const fetchStatus = async () => {
      try {
        const resp = await fetch(`${apiBase()}/api/scan/${token}/status`);
        if (!resp.ok) return;
        const data = await resp.json();
        setMode(String(data?.mode || ""));
        setStatus(String(data?.status || "pending"));
      } catch {
        // ignore
      }
    };
    fetchStatus();
  }, [token]);

  const handleFileChange = async (event) => {
    const file = event.target.files?.[0];
    if (!file || uploading) return;
    setUploading(true);
    setMessage("Uploading...");
    const uploadUrl = `${apiBase()}/api/scan/${token}/upload`;
    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await fetch(uploadUrl, {
        method: "POST",
        body: form,
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = data?.detail || "Upload failed";
        console.error("Scan upload failed", {
          url: uploadUrl,
          status: resp.status,
          statusText: resp.statusText,
        });
        const errorMessage = `Status ${resp.status}: ${detail}`;
        toast({
          variant: "destructive",
          title: "Upload failed",
          description: errorMessage,
        });
        setMessage(errorMessage);
        return;
      }
      setStatus("complete");
      if (mode === "master_key") {
        setMessage("Master key saved ✅ You can close this tab.");
      } else {
        setMessage("Saved ✅ Scan next.");
      }
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      console.error("Scan upload error", { url: uploadUrl, error: err });
      const errorMessage = `Status network_error: ${err?.message || "Upload failed"}`;
      toast({
        variant: "destructive",
        title: "Upload failed",
        description: errorMessage,
      });
      setMessage(errorMessage);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-slate-900 p-6">
      <div className="max-w-md mx-auto space-y-4">
        <h1 className="text-2xl font-semibold">{scanLabel(mode)}</h1>
        <p className="text-sm text-slate-600">
          Capture a clean, centered scan. Hold steady and fill the frame.
        </p>
        {status === "expired" && (
          <p className="text-sm text-red-600">This scan session expired. Scan a new QR.</p>
        )}
        <div className="rounded-lg border border-slate-200 p-4 space-y-3">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            capture="environment"
            onChange={handleFileChange}
            disabled={status === "expired"}
            className="hidden"
          />
          <Button
            variant="secondary"
            onClick={() => fileRef.current?.click()}
            disabled={status === "expired" || uploading}
          >
            {uploading ? "Uploading..." : "Capture photo"}
          </Button>
          {message && <div className="text-sm text-slate-700">{message}</div>}
          {mode === "student" && status === "complete" && (
            <div className="text-xs text-slate-500">
              Scan a new QR on desktop for the next student.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
