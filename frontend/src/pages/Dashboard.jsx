import React, {
  useEffect,
  useState,
  useCallback,
  useMemo,
  useRef,
} from "react";
import supa, { previewUrl } from "../lib/supa";

/**
 * UploadPanel â€” UI-only (uses onUpload callback for real work)
 * You can freely restyle this markup later; no backend logic lives inside.
 */
function UploadPanel({
  multiple = true,
  accept = "image/*,application/pdf",
  maxSizeMB = 15,
  maxFiles = 10,
  onUpload, // wired to handleUploadToSupabase below
}) {
  const [files, setFiles] = useState([]);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const inputRef = useRef(null);
  const [successMsg, setSuccessMsg] = useState("");
  const [lastUploaded, setLastUploaded] = useState([]);

  const maxBytes = useMemo(() => maxSizeMB * 1024 * 1024, [maxSizeMB]);

  const readableSize = (bytes) => {
    if (bytes === undefined || bytes === null) return "";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0,
      v = bytes;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
  };

  const validate = useCallback(
    (incoming) => {
      const errs = [];
      const accepted = (mime) =>
        accept.split(",").some((p) => {
          const t = p.trim();
          if (t.endsWith("/*")) return mime.startsWith(t.slice(0, -2));
          return mime === t;
        });

      if (!multiple && incoming.length > 1)
        errs.push("Please select only one file.");
      if (incoming.length + files.length > maxFiles)
        errs.push(`Max ${maxFiles} files.`);
      for (const f of incoming) {
        if (!accepted(f.type)) errs.push(`${f.name}: unsupported type`);
        if (f.size > maxBytes) errs.push(`${f.name}: over ${maxSizeMB}MB`);
      }
      return errs;
    },
    [accept, files.length, maxFiles, maxBytes, maxSizeMB, multiple]
  );

  const addFiles = useCallback(
    (incoming) => {
      const list = Array.from(incoming);
      const errs = validate(list);
      if (errs.length) {
        setError(errs.join("\n"));
        return;
      }
      setError("");
      const key = (f) => `${f.name}__${f.size}`;
      const existing = new Set(files.map(key));
      const merged = [...files];
      for (const f of list) if (!existing.has(key(f))) merged.push(f);
      setFiles(merged);
    },
    [files, validate]
  );

  const onDrop = useCallback(
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      setDragActive(false);
      if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
    },
    [addFiles]
  );

  const onBrowse = () => {
    if (inputRef.current) {
      inputRef.current.value = "";   // allow re-selecting same files
      inputRef.current.click();
    }
  };
  const removeAt = (idx) =>
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  const clear = () => {
    setFiles([]);
    setProgress(0);
    setError("");
  };

  const handleUpload = async () => {
    if (!files.length) {               // no files â†’ open picker instead of error
      if (inputRef.current) inputRef.current.click();
      return;
    }
    setError("");
    setIsUploading(true);
    setProgress(5);
    try {
      if (onUpload) {
        const result = await onUpload(files, setProgress);
        setLastUploaded(Array.isArray(result) ? result : []);
        setSuccessMsg(`Uploaded ${Array.isArray(result) ? result.length : files.length} file(s) successfully.`);
        setFiles([]);                  // auto-clear selection after success
        setProgress(0);                // reset progress bar
        setTimeout(() => setSuccessMsg(""), 3500); // hide message
      } else {
        // fallback simulate (not used, since we pass onUpload)
        await new Promise((r) => setTimeout(r, 400));
        setProgress(35);
        await new Promise((r) => setTimeout(r, 400));
        setProgress(65);
        await new Promise((r) => setTimeout(r, 500));
        setProgress(100);
        await new Promise((r) => setTimeout(r, 250));
      }
    } catch (err) {
      console.error(err);
      setError(err?.message || "Upload failed. Try again.");
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="upload-wrap">
      <div className="upload-head">
        <h2>Upload worksheets</h2>
        <p>
          Images or PDF Â· {multiple ? `Up to ${maxFiles} files` : "Single file"} Â· â‰¤{" "}
          {maxSizeMB}MB each
        </p>
      </div>

      {successMsg && (
        <div
          role="status"
          aria-live="polite"
          style={{
            margin: "12px 0 16px",
            border: "1px solid #bbf7d0",
            background: "#ecfdf5",
            color: "#065f46",
            borderRadius: 10,
            padding: "10px 12px",
            fontSize: 14,
            fontWeight: 600,
          }}
        >
          {successMsg}
        </div>
      )}

      <div
        role="button"
        tabIndex={0}
        onClick={onBrowse}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onBrowse()}
        onDragEnter={() => setDragActive(true)}
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
        aria-label="Upload files"
        className={`dropzone ${dragActive ? "dropzone--active" : ""}`}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple={multiple}
          onChange={(e) => e.target.files && addFiles(e.target.files)}
          style={{ display: "none" }}
        />
        <div className="dropzone-inner">
          <div className="dz-title">Drag files here</div>
          <div className="dz-sub">
            or <span className="dz-browse">browse</span> from your computer
          </div>
        </div>
      </div>

      {error && (
        <div className="upload-error">
          {error.split("\n").map((ln, i) => (
            <div key={i}>{ln}</div>
          ))}
        </div>
      )}

      {!!files.length && (
        <div className="file-list">
          <div className="file-list-head">Selected</div>
          <ul className="file-chips">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="chip">
                <span className="chip-name" title={f.name}>
                  {f.name}
                </span>
                <span className="chip-size">{readableSize(f.size)}</span>
                <button
                  type="button"
                  onClick={() => removeAt(i)}
                  className="chip-x"
                  aria-label={`Remove ${f.name}`}
                >
                  Ã—
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="upload-actions">
        <button
          type="button"
          onClick={handleUpload}
          disabled={isUploading}
          className="btn btn-primary"
        >
          {isUploading ? "Uploadingâ€¦" : "Upload"}
        </button>
        <button
          type="button"
          onClick={onBrowse}
          disabled={isUploading}
          className="btn btn-ghost"
        >
          Add files
        </button>
        <button
          type="button"
          onClick={clear}
          disabled={isUploading || !files.length}
          className="btn btn-ghost"
        >
          Clear
        </button>
        <div className="accept-note">
          Accepted: {accept.replaceAll(",", ", ")}
        </div>
      </div>

      {(isUploading || progress > 0) && (
        <div className="progress-wrap" style={{ marginTop: 12 }}>
          {isUploading && (
            <div style={{ fontSize: 12, color: "#475467", marginBottom: 6 }}>
              Uploadingâ€¦ ({files.length} file{files.length > 1 ? "s" : ""})
            </div>
          )}
          <div className="progress-bar" style={{ height: 8, borderRadius: 999, background: "#f2f4f7", overflow: "hidden" }}>
            <div
              className="progress-fill"
              style={{ height: 8, background: "#4f46e5", borderRadius: 999, width: `${progress}%`, transition: "width 0.2s ease" }}
            />
          </div>
          <div className="progress-text" style={{ marginTop: 4, fontSize: 12, color: "#667085" }}>
            {progress === 100 && !isUploading ? "Completed" : `${progress}%`}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Upload each file to Supabase Storage:
 * - bucket: submissions (private)
 * - key: submissions/{auth.uid()}/{unique}-{cleanOriginalName}
 *
 * NOTE: we do NOT append an extra extension if the original name already has one.
 */
async function handleUploadToSupabase(files, setProgress) {
  // 1) Ensure user is logged in
  const { data: userData, error: userErr } = await supa.auth.getUser();
  if (userErr || !userData?.user) {
    throw new Error("You must be signed in to upload.");
  }
  const userId = userData.user.id;

  // 2) Target bucket
  const BUCKET = "submissions";

  // 3) Upload loop
  const total = files.length;
  const uploaded = [];

  for (let i = 0; i < total; i++) {
    const f = files[i];
    const orig = f.name || `file-${i}`;

    // Clean original name; keep its extension as-is
    const clean = orig
      .replace(/\s+/g, "_")
      .replace(/[^a-zA-Z0-9._-]/g, "");

    const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    // Do NOT double-append .ext â€” clean already contains it
    const objectKey = `${userId}/${unique}-${clean}`;

    const { data: upData, error } = await supa.storage
      .from(BUCKET)
      .upload(objectKey, f, {
        contentType: f.type || "application/octet-stream",
        upsert: false,
      });

    if (error) {
      throw new Error(`Failed to upload ${orig}: ${error.message}`);
    }

    // use the canonical path Supabase returns (never recompute yourself)
    const savedKey = upData?.path || objectKey;

    // Optional quick check, verify the object is reachable using helper
    try {
      const res = await previewUrl(BUCKET, savedKey);
      console.log("preview url ok?", !!res?.url, savedKey);
    } catch {}

    uploaded.push({
      path: savedKey,
      originalName: orig,
      size: f.size,
      type: f.type,
    });

    // Per-file progress (Supabase Storage SDK doesn't expose byte progress)
    setProgress(Math.round(((i + 1) / total) * 100));
  }

  return uploaded;
}

export default function Dashboard() {
  const [email, setEmail] = useState(null);
  // === Sprint 2E: assignments + uploads metadata ===
  const [assignments, setAssignments] = useState([]);
  const [selectedAssignmentId, setSelectedAssignmentId] = useState(null);
  const [loadingAssignments, setLoadingAssignments] = useState(false);

  const [myFiles, setMyFiles] = useState([]);      // replace your old myFiles if you had one
  const [loadingList, setLoadingList] = useState(false);
  const BUCKET = "submissions";

  // New-assignment inline form (moved to Assignments page)

  useEffect(() => {
    supa.auth.getUser().then(({ data }) => {
      setEmail(data.user?.email ?? null);
    });
  }, []);

  // Accept deep-link assignmentId from URL once on mount
  useEffect(() => {
    const idFromUrl = new URLSearchParams(window.location.search).get("assignmentId");
    if (idFromUrl) setSelectedAssignmentId(idFromUrl);
  }, []);

  async function loadAssignments() {
    setLoadingAssignments(true);
    try {
      const { data: userData } = await supa.auth.getUser();
      const userId = userData?.user?.id;
      if (!userId) { setAssignments([]); return; }

      const { data, error } = await supa
        .from("assignments")
        .select("id,title,due_date,created_at")
        .eq("owner_id", userId)
        .order("created_at", { ascending: false });

      if (error) throw error;
      setAssignments(data || []);
      if (!selectedAssignmentId && (data?.length ?? 0) > 0) {
        setSelectedAssignmentId(data[0].id);
      }
    } catch (e) {
      console.error(e);
      setAssignments([]);
    } finally {
      setLoadingAssignments(false);
    }
  }

  // createAssignment UI moved to Assignments page

  // List uploads for this teacher, filtered by selected assignment (or unassigned)
  async function refreshList() {
    setLoadingList(true);
    try {
      const { data: userData } = await supa.auth.getUser();
      const userId = userData?.user?.id;
      if (!userId) { setMyFiles([]); return; }

      let query = supa
        .from("uploads")
        .select("id, storage_path, original_name, mime_type, size_bytes, uploaded_at, assignment_id")
        .eq("owner_id", userId)
        .order("uploaded_at", { ascending: false });

      if (selectedAssignmentId) {
        query = query.eq("assignment_id", selectedAssignmentId);
      } else {
        query = query.is("assignment_id", null);
      }

      const { data, error } = await query;
      if (error) throw error;

      const withUrls = await Promise.all(
        (data || []).map(async (row) => {
          const name = row.original_name || row.storage_path.split("/").pop();
          const key = row.storage_path?.startsWith(`${BUCKET}/`) ? row.storage_path.slice(BUCKET.length + 1) : row.storage_path;
          const urlRes = await previewUrl(BUCKET, key);
          return {
            id: row.id,
            path: row.storage_path,
            name,
            size: row.size_bytes ?? 0,
            signedUrl: urlRes.ok ? urlRes.url : null,
            isImage: /\.(png|jpe?g|gif|webp)$/i.test(name),
            isPDF: /\.pdf$/i.test(name),
          };
        })
      );

      setMyFiles(withUrls);
    } catch (e) {
      console.error(e);
      setMyFiles([]);
    } finally {
      setLoadingList(false);
    }
  }

  useEffect(() => { loadAssignments(); }, []);
  useEffect(() => { refreshList(); }, [selectedAssignmentId]);

  const signOut = async () => {
    await supa.auth.signOut();
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
        {/* Assignment selector */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap" }}>
            <div>
              <label style={{ fontSize: 13, color: "#667085" }}>Assignment</label><br/>
              <select
                value={selectedAssignmentId || ""}
                onChange={(e) => setSelectedAssignmentId(e.target.value || null)}
                disabled={loadingAssignments}
                style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #e4e7ec" }}
              >
                <option value="">Unassigned</option>
                {assignments.map(a => (
                  <option key={a.id} value={a.id}>
                    {a.title}{a.due_date ? ` (${a.due_date})` : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <UploadPanel
          multiple
          maxSizeMB={15}
          maxFiles={10}
          accept="image/*,application/pdf"
          onUpload={async (files, setProgress) => {
            const uploaded = await handleUploadToSupabase(files, setProgress);

            // Insert upload rows (same as before)
            const { data: userData } = await supa.auth.getUser();
            const userId = userData?.user?.id;
            if (userId && uploaded?.length) {
              const rows = uploaded.map(u => ({
                owner_id: userId,
                assignment_id: selectedAssignmentId ?? null,  // selected assignment OR unassigned
                storage_path: u.path,
                original_name: u.originalName,
                mime_type: u.type || null,
                size_bytes: u.size ?? null,
                status: "pending",
              }));
              const { error } = await supa.from("uploads").insert(rows);
              if (error) console.error(error);
            }

            // ðŸ‘‰ After upload, go to Assignments (filtered to the chosen folder)
            const dest = selectedAssignmentId
              ? `/assignments?assignmentId=${selectedAssignmentId}`
              : `/assignments?filter=unassigned`;
            window.location.href = dest;

            return uploaded;
          }}
        />

        {false && (
          <div style={{ marginTop: 24 }}>
            <h3 style={{ marginBottom: 8 }}>My uploads</h3>
            {loadingList && <div>Loadingâ€¦</div>}
            {!loadingList && myFiles.length === 0 && <div>No files yet.</div>}
            {!loadingList && myFiles.length > 0 && (
              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 12 }}>
                {myFiles.map((f) => (
                  <li key={f.path} style={{ border: "1px solid #e4e7ec", borderRadius: 12, padding: 12 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <div style={{ fontWeight: 600 }}>{f.name}</div>
                        <div style={{ fontSize: 12, color: "#667085" }}>{(f.size/1024).toFixed(1)} KB</div>
                      </div>
                      {f.signedUrl ? (
                        <a href={f.signedUrl} target="_blank" rel="noreferrer">Open</a>
                      ) : (
                        <span style={{ fontSize: 12, color: "#667085" }}>No preview</span>
                      )}
                    </div>

                    {f.isImage && f.signedUrl && (
                      <img src={f.signedUrl} alt={f.name} style={{ marginTop: 8, maxWidth: "100%", borderRadius: 8 }} />
                    )}
                    {f.isPDF && f.signedUrl && (
                      <div style={{ marginTop: 8 }}>
                        <a href={f.signedUrl} target="_blank" rel="noreferrer">Preview PDF</a>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div style={{ marginTop: 24 }}>Classes (coming next)</div>
        <div>Assignments (coming next)</div>
      </main>
    </div>
  );
}

