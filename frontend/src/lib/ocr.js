// frontend/src/lib/ocr.js
import { supabase } from "@/lib/supabaseClient";

const BASE = "https://www.handwritingocr.com/api/v3";

// --- small helpers ---
async function fetchWithRateHandling(url, opts, tries = 3) {
  const res = await fetch(url, opts);
  if (res.status === 429 && tries > 0) {
    const wait = parseInt(res.headers.get("Retry-After") || "1", 10) * 1000;
    await new Promise((r) => setTimeout(r, Math.max(wait, 1000)));
    return fetchWithRateHandling(url, opts, tries - 1);
  }
  return res;
}

// join page transcripts -> one string
function joinTranscripts(json) {
  if (!json?.results) return "";
  return json.results
    .map((p) => (p.transcript ?? "").trim())
    .filter(Boolean)
    .join("\n\n");
}

// Build a streaming multipart/form-data body so we never buffer the whole file.
// We stream: prefix (fields + file headers) -> sourceStream -> suffix.
function makeStreamingMultipartBody(sourceStream, { filename, contentType = "application/octet-stream", fields = {} }) {
  const enc = new TextEncoder();
  const boundary = "----hocr_" + Math.random().toString(16).slice(2);

  // multipart prefix (fields + beginning of file part)
  let prefix = "";
  for (const [k, v] of Object.entries(fields)) {
    prefix += `--${boundary}\r\n`;
    prefix += `Content-Disposition: form-data; name="${k}"\r\n\r\n`;
    prefix += `${v}\r\n`;
  }
  prefix += `--${boundary}\r\n`;
  prefix += `Content-Disposition: form-data; name="file"; filename="${filename}"\r\n`;
  prefix += `Content-Type: ${contentType}\r\n\r\n`;

  // multipart suffix (end boundary)
  const suffix = `\r\n--${boundary}--\r\n`;

  const prefixBytes = enc.encode(prefix);
  const suffixBytes = enc.encode(suffix);

  // Compose a ReadableStream that yields prefix -> file bytes -> suffix
  const bodyStream = new ReadableStream({
    start(controller) {
      controller.enqueue(prefixBytes);
    },
    async pull(controller) {
      // once we’ve enqueued prefix, start piping the source stream
      if (!this._reader) {
        this._reader = sourceStream.getReader();
        return;
      }
      const { value, done } = await this._reader.read();
      if (done) {
        controller.enqueue(suffixBytes);
        controller.close();
        return;
      }
      controller.enqueue(value);
    },
    cancel(reason) {
      try { this._reader?.cancel(reason); } catch {}
    },
  });

  return { bodyStream, boundary };
}

// Calls OCR for one upload row and updates DB fields.
// row must include: { id, storage_path }
export async function runOcrForUpload(row) {
  // 1) mark processing
  await supabase
    .from("uploads")
    .update({
      status: "processing",
      ocr_started_at: new Date().toISOString(),
      ocr_error: null,
    })
    .eq("id", row.id);

  // 2) signed URL (10 min)
  const { data: signed, error: signErr } = await supabase
    .storage.from("submissions")
    .createSignedUrl(row.storage_path, 60 * 10);
  if (signErr) {
    await supabase
      .from("uploads")
      .update({
        status: "error",
        ocr_error: `Sign URL failed: ${signErr.message}`,
      })
      .eq("id", row.id);
    throw signErr;
  }

  // 3) stream download from signed URL (no Blob)
  let resp;
  try {
    resp = await fetch(signed.signedUrl);
    if (!resp.ok || !resp.body) {
      throw new Error(`Signed URL fetch ${resp.status}`);
    }
  } catch (e) {
    await supabase.from("uploads").update({
      status: "error",
      ocr_error: String(e),
    }).eq("id", row.id);
    throw e;
  }

  const filename = row.storage_path.split("/").pop() || "upload";
  const contentType = resp.headers.get("content-type") || "application/octet-stream";

  // 4) Build streaming multipart body (fields + streamed file)
  const { bodyStream, boundary } = makeStreamingMultipartBody(resp.body, {
    filename,
    contentType,
    fields: { action: "transcribe" },
  });

  // 5) Upload to HandwritingOCR (multipart, streamed)
  let id;
  try {
    const up = await fetchWithRateHandling(`${BASE}/documents`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${import.meta.env.VITE_HOCR_KEY}`,
        Accept: "application/json",
        // IMPORTANT: we set the boundary ourselves
        "Content-Type": `multipart/form-data; boundary=${boundary}`,
      },
      body: bodyStream, // ReadableStream — streamed, no full buffering
    });
    if (!up.ok) throw new Error(`Upload HTTP ${up.status}`);
    const json = await up.json();
    id = json?.id;
    if (!id) throw new Error("No document id returned");
  } catch (e) {
    await supabase
      .from("uploads")
      .update({ status: "error", ocr_error: String(e) })
      .eq("id", row.id);
    throw e;
  }

  // 6) Poll for result
  let text = "";
  for (let i = 0; i < 40; i++) {
    const res = await fetchWithRateHandling(`${BASE}/documents/${id}.json`, {
      headers: {
        Authorization: `Bearer ${import.meta.env.VITE_HOCR_KEY}`,
        Accept: "application/json",
      },
    });
    if (res.status === 202) {
      await new Promise((r) => setTimeout(r, 1000));
      continue;
    }
    if (!res.ok) throw new Error(`Status HTTP ${res.status}`);
    const data = await res.json();
    if (data?.status === "processed") {
      text = joinTranscripts(data);
      break;
    }
    if (data?.status === "failed") {
      throw new Error("OCR processing failed");
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  if (!text) {
    await supabase
      .from("uploads")
      .update({ status: "error", ocr_error: "Timeout waiting for OCR" })
      .eq("id", row.id);
    throw new Error("Timeout waiting for OCR");
  }

  // 7) store results
  await supabase
    .from("uploads")
    .update({
      status: "done",
      ocr_text: text,
      ocr_completed_at: new Date().toISOString(),
    })
    .eq("id", row.id);

  return text;
}
