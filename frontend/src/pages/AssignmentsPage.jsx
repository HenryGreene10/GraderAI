import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "../lib/apiBase";
import supa from "../lib/supa";

const ACCEPTED_MIME = ["image/png", "image/jpeg", "application/pdf"];
const ACCEPTED_EXT = [".png", ".jpg", ".jpeg", ".pdf"];

function isAllowedFile(file) {
  if (!file) return false;
  if (ACCEPTED_MIME.includes(file.type)) return true;
  const name = String(file.name || "").toLowerCase();
  return ACCEPTED_EXT.some((ext) => name.endsWith(ext));
}

function statusLabel(status) {
  const normalized = String(status || "uploaded").toLowerCase();
  if (normalized === "overridden") return "Overridden";
  if (normalized === "reviewed") return "Reviewed";
  if (normalized === "pending" || normalized === "uploaded") return "Uploaded";
  if (normalized === "processing" || normalized === "running") return "Processing";
  if (normalized === "failed" || normalized === "error") return "Needs review";
  return normalized.replace(/_/g, " ");
}

function isPdf(upload) {
  return (
    String(upload?.mime_type || "").includes("pdf") ||
    String(upload?.original_name || "").toLowerCase().endsWith(".pdf")
  );
}

function isImage(upload) {
  const name = String(upload?.original_name || "").toLowerCase();
  if (name.endsWith(".png") || name.endsWith(".jpg") || name.endsWith(".jpeg")) return true;
  return String(upload?.mime_type || "").startsWith("image/");
}

export default function AssignmentsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { toast } = useToast();

  const assignmentId = useMemo(() => {
    return new URLSearchParams(location.search).get("assignmentId");
  }, [location.search]);

  const [assignment, setAssignment] = useState(null);
  const [uploads, setUploads] = useState([]);
  const [loading, setLoading] = useState(false);

  const [uploadOpen, setUploadOpen] = useState(false);
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);

  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewFile, setPreviewFile] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteAssignmentOpen, setDeleteAssignmentOpen] = useState(false);
  const [deletingAssignment, setDeletingAssignment] = useState(false);
  const [grading, setGrading] = useState({});
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideTarget, setOverrideTarget] = useState(null);
  const [overrideStatus, setOverrideStatus] = useState("correct");
  const [overrideNote, setOverrideNote] = useState("");
  const [overrideSaving, setOverrideSaving] = useState(false);

  useEffect(() => {
    if (!uploadOpen) {
      setFiles([]);
      setUploading(false);
    }
  }, [uploadOpen]);

  useEffect(() => {
    if (!assignmentId) return;
    loadAssignment();
    loadUploads();
  }, [assignmentId]);

  async function loadAssignment() {
    try {
      const resp = await apiFetch("/api/assignments");
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Failed: ${resp.status}`);
      }
      const data = await resp.json();
      const match = (data.assignments || []).find((row) => row.id === assignmentId);
      setAssignment(match || null);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Failed to load assignment",
        description: err?.message || "Try again.",
      });
    }
  }

  async function loadUploads() {
    if (!assignmentId) return;
    setLoading(true);
    try {
      const resp = await apiFetch(`/api/assignments/${assignmentId}/uploads`);
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Failed: ${resp.status}`);
      }
      const data = await resp.json();
      setUploads(data.uploads || []);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Failed to load uploads",
        description: err?.message || "Try again.",
      });
      setUploads([]);
    } finally {
      setLoading(false);
    }
  }

  const addFiles = (incoming) => {
    const list = Array.from(incoming || []);
    if (!list.length) return;
    const rejected = list.filter((file) => !isAllowedFile(file));
    if (rejected.length) {
      toast({
        variant: "destructive",
        title: "Unsupported file type",
        description: rejected.map((f) => f.name).join(", "),
      });
    }
    const accepted = list.filter(isAllowedFile);
    setFiles((prev) => {
      const existing = new Set(prev.map((f) => `${f.name}-${f.size}`));
      const merged = [...prev];
      accepted.forEach((file) => {
        const key = `${file.name}-${file.size}`;
        if (!existing.has(key)) merged.push(file);
      });
      return merged;
    });
  };

  const removeFileAt = (index) => {
    setFiles((prev) => prev.filter((_, idx) => idx !== index));
  };

  const openPicker = () => {
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
      fileInputRef.current.click();
    }
  };

  async function handleUpload() {
    if (!assignmentId) return;
    if (files.length === 0) {
      toast({ variant: "destructive", title: "Add at least one file" });
      return;
    }
    setUploading(true);
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append("files", file));
      const resp = await apiFetch(`/api/assignments/${assignmentId}/uploads`, {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Upload failed: ${resp.status}`);
      }
      toast({
        title: "Uploads added",
        description: `Uploaded ${files.length} file${files.length > 1 ? "s" : ""}.`,
      });
      setUploadOpen(false);
      await loadUploads();
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Upload failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setUploading(false);
    }
  }

  async function handlePreview(upload) {
    setPreviewFile(upload);
    setPreviewUrl("");
    setPreviewLoading(true);
    setPreviewOpen(true);
    try {
      const resp = await apiFetch(`/api/uploads/${upload.id}/preview`);
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Preview failed: ${resp.status}`);
      }
      const data = await resp.json();
      setPreviewUrl(data.url || "");
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Preview failed",
        description: err?.message || "Try again.",
      });
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      const resp = await apiFetch(`/api/uploads/${deleteTarget.id}`, { method: "DELETE" });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Delete failed: ${resp.status}`);
      }
      toast({ title: "Upload deleted" });
      setDeleteOpen(false);
      setDeleteTarget(null);
      await loadUploads();
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Delete failed",
        description: err?.message || "Try again.",
      });
    }
  }

  async function handleGenerateMarked(upload) {
    const key = upload?.id;
    if (!key) return;
    setGrading((prev) => ({ ...prev, [key]: true }));
    try {
      const resp = await apiFetch(`/api/uploads/${upload.id}/grade`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = data?.detail || `Failed: ${resp.status}`;
        throw new Error(detail);
      }
      toast({ title: "Marked PDF generated" });
      await loadUploads();
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Generate failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setGrading((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  }

  async function handleOpenMarked(upload) {
    const key = upload?.graded_pdf_path;
    if (!key) return;
    try {
      const normalized = String(key).replace(/^\/+/, "");
      const path = normalized.startsWith("graded-pdfs/")
        ? normalized.slice("graded-pdfs/".length)
        : normalized;
      const { data, error } = await supa.storage.from("graded-pdfs").createSignedUrl(path, 3600);
      if (error) throw new Error(error.message || "Signed URL failed");
      if (!data?.signedUrl) throw new Error("Missing signed URL");
      window.open(data.signedUrl, "_blank");
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Open marked PDF failed",
        description: err?.message || "Try again.",
      });
    }
  }

  async function handleDeleteAssignment() {
    if (!assignmentId || deletingAssignment) return;
    setDeletingAssignment(true);
    try {
      const resp = await apiFetch(`/api/assignments/${assignmentId}`, { method: "DELETE" });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Delete failed: ${resp.status}`);
      }
      toast({ title: "Assignment deleted" });
      setDeleteAssignmentOpen(false);
      navigate("/");
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Delete assignment failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setDeletingAssignment(false);
    }
  }

  function openOverride(upload) {
    setOverrideTarget(upload);
    setOverrideStatus("correct");
    setOverrideNote("");
    setOverrideOpen(true);
  }

  async function handleSaveOverride() {
    if (!overrideTarget || overrideSaving) return;
    setOverrideSaving(true);
    try {
      const resp = await apiFetch(`/api/uploads/${overrideTarget.id}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          overall_status: overrideStatus,
          note: overrideNote.trim() || null,
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = data?.detail || `Failed: ${resp.status}`;
        throw new Error(detail);
      }
      const nextStatus = data.status || (overrideStatus === "reviewed" ? "reviewed" : "overridden");
      setUploads((prev) =>
        prev.map((row) =>
          row.id === overrideTarget.id
            ? { ...row, status: nextStatus }
            : row
        )
      );
      toast({ title: "Override saved" });
      setOverrideOpen(false);
      setOverrideTarget(null);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Override failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setOverrideSaving(false);
    }
  }

  if (!assignmentId) {
    return (
      <div className="p-6 space-y-4">
        <h1 className="text-2xl font-semibold">Assignment uploads</h1>
        <p className="text-sm text-muted-foreground">
          Choose an assignment from the dashboard to view uploads.
        </p>
        <Button variant="outline" onClick={() => navigate("/")}>Back to dashboard</Button>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <Button variant="ghost" size="sm" onClick={() => navigate("/")}>Back</Button>
          <h1 className="text-2xl font-semibold mt-2">{assignment?.title || "Assignment"}</h1>
          {assignment?.description && (
            <p className="text-sm text-muted-foreground">{assignment.description}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
            <DialogTrigger asChild>
              <Button>Upload files</Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[520px]">
              <DialogHeader>
                <DialogTitle>Upload files</DialogTitle>
                <DialogDescription>
                  Add more student files to this assignment.
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-3">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept={ACCEPTED_MIME.join(",")}
                  className="hidden"
                  onChange={(e) => addFiles(e.target.files)}
                />
                <Button variant="secondary" type="button" onClick={openPicker}>
                  Add files
                </Button>

                {files.length > 0 ? (
                  <div className="max-h-48 overflow-auto rounded-md border border-border p-2">
                    <div className="space-y-2">
                      {files.map((file, idx) => (
                        <div key={`${file.name}-${file.size}`} className="flex items-center justify-between gap-2 text-sm">
                          <div className="truncate">{file.name}</div>
                          <Button size="sm" variant="ghost" onClick={() => removeFileAt(idx)}>
                            Remove
                          </Button>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
                    PNG, JPG, or PDF only.
                  </div>
                )}
              </div>

              <DialogFooter className="gap-2 sm:gap-0">
                <Button variant="outline" onClick={() => setUploadOpen(false)} disabled={uploading}>
                  Cancel
                </Button>
                <Button onClick={handleUpload} disabled={files.length === 0 || uploading}>
                  {uploading ? "Uploading..." : "Upload"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button variant="destructive" onClick={() => setDeleteAssignmentOpen(true)}>
            Delete assignment
          </Button>
        </div>
      </header>

      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Filename</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && (
              <TableRow>
                <TableCell colSpan={3} className="text-center text-muted-foreground">
                  Loading uploads...
                </TableCell>
              </TableRow>
            )}
            {!loading && uploads.length === 0 && (
              <TableRow>
                <TableCell colSpan={3} className="text-center text-muted-foreground">
                  No uploads yet.
                </TableCell>
              </TableRow>
            )}
            {uploads.map((upload) => (
              <TableRow key={upload.id}>
                <TableCell className="font-medium">{upload.original_name || "Untitled"}</TableCell>
                <TableCell>
                  <Badge variant="secondary">{statusLabel(upload.status)}</Badge>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="outline" onClick={() => handlePreview(upload)}>
                      Preview
                    </Button>
                    {upload.graded_pdf_path && (
                      <Button size="sm" variant="outline" onClick={() => handleOpenMarked(upload)}>
                        Open marked PDF
                      </Button>
                    )}
                    {upload.graded_pdf_path && (
                      <Button size="sm" variant="outline" onClick={() => openOverride(upload)}>
                        Override
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={grading[upload.id] || String(upload.ocr_status || "").toLowerCase() !== "done"}
                      onClick={() => handleGenerateMarked(upload)}
                      title={
                        String(upload.ocr_status || "").toLowerCase() !== "done"
                          ? "OCR not complete"
                          : undefined
                      }
                    >
                      {grading[upload.id] ? "Generating..." : "Generate marked PDF"}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => {
                        setDeleteTarget(upload);
                        setDeleteOpen(true);
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>Preview</DialogTitle>
            <DialogDescription>{previewFile?.original_name}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {previewLoading && (
              <div className="text-sm text-muted-foreground">Loading preview...</div>
            )}
            {!previewLoading && previewUrl && isImage(previewFile) && (
              <img
                src={previewUrl}
                alt={previewFile?.original_name || "Preview"}
                className="max-h-60 w-full rounded-md object-contain"
              />
            )}
            {!previewLoading && previewUrl && isPdf(previewFile) && (
              <a
                className="text-sm text-primary underline"
                href={previewUrl}
                target="_blank"
                rel="noreferrer"
              >
                Open PDF in new tab
              </a>
            )}
            {!previewLoading && previewUrl && !isImage(previewFile) && !isPdf(previewFile) && (
              <a
                className="text-sm text-primary underline"
                href={previewUrl}
                target="_blank"
                rel="noreferrer"
              >
                Open file
              </a>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewOpen(false)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete upload?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the file and its record. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDeleteTarget(null)}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={deleteAssignmentOpen} onOpenChange={setDeleteAssignmentOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete assignment “{assignment?.title || "Assignment"}”?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This will delete all uploads inside it. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingAssignment}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteAssignment} disabled={deletingAssignment}>
              {deletingAssignment ? "Deleting..." : "Delete assignment"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={overrideOpen} onOpenChange={setOverrideOpen}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>Override grade</DialogTitle>
            <DialogDescription>
              Adjust the overall result for this upload.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Status</label>
              <select
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={overrideStatus}
                onChange={(e) => setOverrideStatus(e.target.value)}
              >
                <option value="correct">Correct</option>
                <option value="partial">Partial</option>
                <option value="incorrect">Incorrect</option>
                <option value="reviewed">Reviewed</option>
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Note (optional)</label>
              <textarea
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                rows={3}
                value={overrideNote}
                onChange={(e) => setOverrideNote(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOverrideOpen(false)} disabled={overrideSaving}>
              Cancel
            </Button>
            <Button onClick={handleSaveOverride} disabled={overrideSaving}>
              {overrideSaving ? "Saving..." : "Save override"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  );
}
