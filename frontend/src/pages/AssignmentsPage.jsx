import React, { useEffect, useMemo, useState } from "react";
import { supabase } from "../lib/supabaseClient";

export default function AssignmentsPage() {
  const [email, setEmail] = useState(null);

  // folders
  const [assignments, setAssignments] = useState([]);
  const [loadingAssignments, setLoadingAssignments] = useState(false);

  // which “folder” is open? null = Unassigned
  const [selectedAssignmentId, setSelectedAssignmentId] = useState(
    new URLSearchParams(window.location.search).get("assignmentId") || null
  );
  const forcedUnassigned =
    new URLSearchParams(window.location.search).get("filter") === "unassigned";

  // files for current folder
  const [files, setFiles] = useState([]);
  const [loadingFiles, setLoadingFiles] = useState(false);

  // create folder (assignment)
  const [newTitle, setNewTitle] = useState("");
  const [newDue, setNewDue] = useState("");
  const [creating, setCreating] = useState(false);

  // selection for bulk actions
  const [checked, setChecked] = useState({}); // { id: true }
  const selectedIds = useMemo(
    () => Object.entries(checked).filter(([, v]) => v).map(([k]) => k),
    [checked]
  );

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
  }, []);

  // load folders
  async function loadAssignments() {
    setLoadingAssignments(true);
    try {
      const { data: userData } = await supabase.auth.getUser();
      const userId = userData?.user?.id;
      if (!userId) { setAssignments([]); return; }

      const { data, error } = await supabase
        .from("assignments")
        .select("id,title,due_date,created_at")
        .eq("owner_id", userId)
        .order("created_at", { ascending: false });

      if (error) throw error;
      setAssignments(data || []);

      // if we came from /assignments?filter=unassigned keep null
      if (!forcedUnassigned && !selectedAssignmentId && (data?.length ?? 0) > 0) {
        setSelectedAssignmentId(data[0].id);
      }
    } catch (e) {
      console.error(e);
      setAssignments([]);
    } finally {
      setLoadingAssignments(false);
    }
  }

  // load files for current folder (or unassigned)
  async function loadFiles() {
    setLoadingFiles(true);
    setChecked({});
    try {
      const { data: userData } = await supabase.auth.getUser();
      const userId = userData?.user?.id;
      if (!userId) { setFiles([]); return; }

      let query = supabase
        .from("uploads")
        .select("id,storage_path,original_name,mime_type,size_bytes,uploaded_at,assignment_id")
        .eq("owner_id", userId)
        .order("uploaded_at", { ascending: false });

      if (selectedAssignmentId) query = query.eq("assignment_id", selectedAssignmentId);
      else query = query.is("assignment_id", null);

      const { data, error } = await query;
      if (error) throw error;

      const withUrls = await Promise.all(
        (data || []).map(async (row) => {
          const { data: urlData } = await supabase.storage
            .from("submissions")
            .createSignedUrl(row.storage_path, 60 * 60);
          return {
            ...row,
            signedUrl: urlData?.signedUrl || null,
            isImage: /\.(png|jpe?g|gif|webp)$/i.test(row.original_name || ""),
            isPDF: /\.pdf$/i.test(row.original_name || ""),
          };
        })
      );
      setFiles(withUrls);
    } catch (e) {
      console.error(e);
      setFiles([]);
    } finally {
      setLoadingFiles(false);
    }
  }

  useEffect(() => { loadAssignments(); }, []);
  useEffect(() => { loadFiles(); }, [selectedAssignmentId, forcedUnassigned]);

  // create new folder
  async function createAssignment() {
    const title = newTitle.trim();
    if (!title) return;
    setCreating(true);
    try {
      const { data: userData } = await supabase.auth.getUser();
      const userId = userData?.user?.id;
      const { data, error } = await supabase
        .from("assignments")
        .insert({ owner_id: userId, title, due_date: newDue || null })
        .select("id,title,due_date,created_at")
        .single();
      if (error) throw error;
      setNewTitle(""); setNewDue("");
      setAssignments((prev) => [data, ...prev]);
      setSelectedAssignmentId(data.id);
    } catch (e) {
      console.error(e);
    } finally {
      setCreating(false);
    }
  }

  // actions: move, rename, delete
  async function moveSelected(toAssignmentId) {
    if (selectedIds.length === 0) return;
    const { error } = await supabase
      .from("uploads")
      .update({ assignment_id: toAssignmentId ?? null })
      .in("id", selectedIds);
    if (error) { console.error(error); return; }
    await loadFiles();
  }

  // inline per-item delete (Storage -> DB)
  async function deleteOne(upload) {
    const storagePath = upload.storage_path;
    const res = await supabase.storage.from("submissions").remove([storagePath]);
    console.log("Storage remove →", { storagePath, res });
    if (res?.error && !/Not Found|not exist/i.test(res.error.message)) {
      throw new Error(`Storage delete failed: ${res.error.message}`);
    }

    const { error: dbErr } = await supabase
      .from("uploads")
      .delete()
      .eq("id", upload.id);
    if (dbErr) throw new Error(`DB delete failed: ${dbErr.message}`);
  }

  async function renameOne(fileId, newName) {
    const clean = newName.trim();
    if (!clean) return;
    const { error } = await supabase
      .from("uploads")
      .update({ original_name: clean })
      .eq("id", fileId);
    // Note: we’re renaming the display name only; storage key remains stable (best practice).
    if (error) { console.error(error); return; }
    await loadFiles();
  }

  async function deleteSelected() {
    if (selectedIds.length === 0) return;
    const { data, error } = await supabase
      .from("uploads")
      .select("id,storage_path")
      .in("id", selectedIds);
    if (error) { console.error(error); return; }

    const rows = data || [];
    const results = await Promise.allSettled(rows.map(deleteOne));

    const failures = results
      .map((r, i) => (r.status === "rejected" ? { row: rows[i], reason: r.reason } : null))
      .filter(Boolean);

    if (failures.length > 0) {
      const msg = failures
        .map((f) => `${f.row.id}: ${f.reason?.message || String(f.reason)}`)
        .join("\n");
      // Replace with your toast lib if available
      alert(`Some items failed to delete:\n${msg}`);
    }

    await loadFiles();
  }

  const otherFolders = [{ id: null, label: "Unassigned" }, ...assignments.map(a => ({ id: a.id, label: a.title }))];

  return (
    <div style={{ padding: 24 }}>
      <header style={{ display: "flex", justifyContent: "space-between" }}>
        <h2>Assignments</h2>
        <div><span style={{ marginRight: 12 }}>{email}</span></div>
      </header>

      {/* Folders bar */}
      <section style={{ marginTop: 12, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            onClick={() => setSelectedAssignmentId(null)}
            className="btn btn-ghost"
            style={{ background: selectedAssignmentId ? "#fff" : "#eef2ff" }}
          >
            Unassigned
          </button>
          {assignments.map((a) => (
            <button
              key={a.id}
              onClick={() => setSelectedAssignmentId(a.id)}
              className="btn btn-ghost"
              style={{ background: selectedAssignmentId === a.id ? "#eef2ff" : "#fff" }}
            >
              {a.title}{a.due_date ? ` (${a.due_date})` : ""}
            </button>
          ))}
        </div>
      </section>

      {/* Create folder */}
      <section style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap" }}>
          <div>
            <label style={{ fontSize: 13, color: "#667085" }}>New folder (assignment)</label><br/>
            <input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="Assignment 1"
              style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #e4e7ec" }}
            />
          </div>
          <div>
            <label style={{ fontSize: 13, color: "#667085" }}>Due date (optional)</label><br/>
            <input
              type="date"
              value={newDue}
              onChange={(e) => setNewDue(e.target.value)}
              style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #e4e7ec" }}
            />
          </div>
          <button onClick={createAssignment} disabled={creating} className="btn btn-primary">
            {creating ? "Adding…" : "Add"}
          </button>
        </div>
      </section>

      {/* Files list for current folder */}
      <section>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>
            {selectedAssignmentId ? "Files in folder" : "Loose files (Unassigned)"}
          </h3>

          {/* Bulk actions */}
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select
              onChange={(e) => {
                const val = e.target.value || null;
                if (val === "__noop__") return;
                moveSelected(val || null);
                e.target.value = "__noop__";
              }}
              defaultValue="__noop__"
              style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #e4e7ec" }}
            >
              <option value="__noop__">Move to…</option>
              {otherFolders.map(f => (
                <option key={String(f.id)} value={f.id || ""}>{f.label}</option>
              ))}
            </select>

            <button onClick={deleteSelected} className="btn btn-ghost" disabled={selectedIds.length === 0}>
              Delete
            </button>
          </div>
        </div>

        {loadingFiles && <div>Loading…</div>}
        {!loadingFiles && files.length === 0 && <div>No files yet.</div>}

        {!loadingFiles && files.length > 0 && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 12 }}>
            {files.map((f) => (
              <li key={f.id} style={{ border: "1px solid #e4e7ec", borderRadius: 12, padding: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <input
                      type="checkbox"
                      checked={!!checked[f.id]}
                      onChange={(e) => setChecked((prev) => ({ ...prev, [f.id]: e.target.checked }))}
                    />
                    <div>
                      <div style={{ fontWeight: 600 }}>{f.original_name}</div>
                      <div style={{ fontSize: 12, color: "#667085" }}>
                        {(f.size_bytes / 1024).toFixed(1)} KB
                      </div>
                      {f.isImage && f.signedUrl && (
                        <img src={f.signedUrl} alt={f.original_name} style={{ marginTop: 8, maxWidth: "100%", borderRadius: 8 }} />
                      )}
                      {f.isPDF && f.signedUrl && (
                        <div style={{ marginTop: 8 }}>
                          <a href={f.signedUrl} target="_blank" rel="noreferrer">Preview PDF</a>
                        </div>
                      )}
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <a href={f.signedUrl || "#"} target="_blank" rel="noreferrer" className="btn btn-ghost">Open</a>
                    <button
                      className="btn btn-ghost"
                      onClick={() => {
                        const nn = prompt("Rename file to:", f.original_name);
                        if (nn != null) renameOne(f.id, nn);
                      }}
                    >
                      Rename
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
