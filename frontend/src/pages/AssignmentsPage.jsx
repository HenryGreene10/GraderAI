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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "../lib/apiBase";
import supa from "../lib/supa";

const ACCEPTED_MIME = ["image/png", "image/jpeg", "application/pdf"];
const ACCEPTED_EXT = [".png", ".jpg", ".jpeg", ".pdf"];
const TEMPLATE_MIME = ["image/png", "image/jpeg"];
const TEMPLATE_EXT = [".png", ".jpg", ".jpeg"];

function isAllowedFile(file) {
  if (!file) return false;
  if (ACCEPTED_MIME.includes(file.type)) return true;
  const name = String(file.name || "").toLowerCase();
  return ACCEPTED_EXT.some((ext) => name.endsWith(ext));
}

function isAllowedTemplate(file) {
  if (!file) return false;
  if (TEMPLATE_MIME.includes(file.type)) return true;
  const name = String(file.name || "").toLowerCase();
  return TEMPLATE_EXT.some((ext) => name.endsWith(ext));
}

function statusLabel(status) {
  const normalized = String(status || "uploaded").toLowerCase();
  if (normalized === "overridden") return "Overridden";
  if (normalized === "reviewed") return "Reviewed";
  if (normalized === "uploading") return "Uploading";
  if (normalized === "ocr_running") return "OCR...";
  if (normalized === "ocr_done") return "OCR done";
  if (normalized === "grading") return "Grading...";
  if (normalized === "pdf_ready") return "PDF ready";
  if (normalized === "pending" || normalized === "uploaded") return "Uploaded";
  if (normalized === "processing" || normalized === "running") return "Processing";
  if (normalized === "failed" || normalized === "error") return "Error";
  return normalized.replace(/_/g, " ");
}

function baseStatus(upload) {
  const status = String(upload?.status || "").toLowerCase();
  const ocrStatus = String(upload?.ocr_status || "").toLowerCase();
  const hasPdf = Boolean(upload?.graded_pdf_path);

  if (status === "error" || ocrStatus === "error") return "error";
  if (status === "grading") return "grading";
  if (status === "ocr_running") return "ocr_running";
  if (status === "ocr_done") return "grading";
  if (status === "uploading" || status === "pending" || status === "uploaded") return "uploading";
  if (status === "graded" || status === "pdf_ready" || hasPdf) return "pdf_ready";
  if (ocrStatus === "pending") return "ocr_running";
  return status || "uploaded";
}

function reviewState(upload) {
  const status = String(upload?.status || "").toLowerCase();
  if ((status === "reviewed" || status === "overridden") && upload?.graded_pdf_path) {
    return status;
  }
  return null;
}

function isProcessing(status) {
  return status === "uploading" || status === "ocr_running" || status === "grading";
}

function isPdf(upload) {
  return (
    String(upload?.mime_type || "").includes("pdf") ||
    String(upload?.original_name || "").toLowerCase().endsWith(".pdf")
  );
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
  const masterKeyInputRef = useRef(null);
  const [masterKeyUploading, setMasterKeyUploading] = useState(false);

  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerTab, setViewerTab] = useState("original");
  const [viewerTarget, setViewerTarget] = useState(null);
  const [viewerUrls, setViewerUrls] = useState({ original: "", marked: "" });
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerError, setViewerError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteAssignmentOpen, setDeleteAssignmentOpen] = useState(false);
  const [deletingAssignment, setDeletingAssignment] = useState(false);
  const [retrying, setRetrying] = useState({});
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

  useEffect(() => {
    if (!assignmentId) return;
    const hasProcessing = uploads.some((u) => isProcessing(baseStatus(u)));
    if (!hasProcessing) return;
    const timer = setInterval(() => {
      loadUploads({ silent: true });
    }, 4000);
    return () => clearInterval(timer);
  }, [uploads, assignmentId]);

  async function loadAssignment() {
    try {
      const resp = await apiFetch(`/api/assignments/${assignmentId}`);
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Failed: ${resp.status}`);
      }
      const data = await resp.json();
      setAssignment(data.assignment || null);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Failed to load assignment",
        description: err?.message || "Try again.",
      });
    }
  }

  async function loadUploads({ silent = false } = {}) {
    if (!assignmentId) return;
    if (!silent) setLoading(true);
    try {
      const resp = await apiFetch(`/api/assignments/${assignmentId}/uploads`);
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Failed: ${resp.status}`);
      }
      const data = await resp.json();
      setUploads(data.uploads || []);
    } catch (err) {
      if (!silent) {
        toast({
          variant: "destructive",
          title: "Failed to load uploads",
          description: err?.message || "Try again.",
        });
      }
      if (!silent) setUploads([]);
    } finally {
      if (!silent) setLoading(false);
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

  const openMasterKeyPicker = () => {
    masterKeyInputRef.current?.click();
  };

  const handleMasterKeySelected = async (file) => {
    if (!file) return;
    if (!isAllowedTemplate(file)) {
      toast({
        variant: "destructive",
        title: "Unsupported file type",
        description: "Master Key must be a PNG or JPG.",
      });
      return;
    }
    if (!assignmentId) return;
    setMasterKeyUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const resp = await apiFetch(`/api/assignments/${assignmentId}/template`, {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Upload failed: ${resp.status}`);
      }
      toast({ title: "Master Key uploaded" });
      await loadAssignment();
      await loadUploads();
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Master Key upload failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setMasterKeyUploading(false);
      if (masterKeyInputRef.current) {
        masterKeyInputRef.current.value = "";
      }
    }
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

  async function fetchOriginalUrl(upload) {
    const resp = await apiFetch(`/api/uploads/${upload.id}/preview`);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(text || `Preview failed: ${resp.status}`);
    }
    const data = await resp.json();
    if (!data?.url) throw new Error("Missing preview URL");
    return data.url;
  }

  async function fetchMarkedUrl(upload) {
    const key = upload?.graded_pdf_path;
    if (!key) return "";
    const normalized = String(key).replace(/^\/+/, "");
    const path = normalized.startsWith("graded-pdfs/")
      ? normalized.slice("graded-pdfs/".length)
      : normalized;
    const { data, error } = await supa.storage.from("graded-pdfs").createSignedUrl(path, 3600);
    if (error) throw new Error(error.message || "Signed URL failed");
    if (!data?.signedUrl) throw new Error("Missing signed URL");
    return data.signedUrl;
  }

  async function openViewer(upload) {
    if (!upload?.id) return;
    setViewerTarget(upload);
    setViewerTab("original");
    setViewerOpen(true);
    setViewerLoading(true);
    setViewerError("");
    setViewerUrls({ original: "", marked: "" });
    try {
      const originalPromise = fetchOriginalUrl(upload);
      const markedPromise = upload.graded_pdf_path ? fetchMarkedUrl(upload) : Promise.resolve("");
      const [originalUrl, markedUrl] = await Promise.all([originalPromise, markedPromise]);
      setViewerUrls({ original: originalUrl, marked: markedUrl });
    } catch (err) {
      setViewerError(err?.message || "Failed to load viewer");
    } finally {
      setViewerLoading(false);
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

  async function handleDownloadOriginal(upload) {
    if (!upload?.id) return;
    try {
      const url = await fetchOriginalUrl(upload);
      window.open(url, "_blank");
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Download failed",
        description: err?.message || "Try again.",
      });
    }
  }

  async function handleDownloadMarked(upload) {
    if (!upload?.id) return;
    try {
      const url = await fetchMarkedUrl(upload);
      if (!url) throw new Error("Marked PDF not ready");
      window.open(url, "_blank");
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Download failed",
        description: err?.message || "Try again.",
      });
    }
  }

  async function handleRetry(upload) {
    if (!upload?.id || retrying[upload.id]) return;
    setRetrying((prev) => ({ ...prev, [upload.id]: true }));
    try {
      const resp = await apiFetch(`/api/uploads/${upload.id}/retry`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = data?.detail || `Failed: ${resp.status}`;
        throw new Error(detail);
      }
      toast({ title: "Retry started" });
      await loadUploads();
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Retry failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setRetrying((prev) => {
        const next = { ...prev };
        delete next[upload.id];
        return next;
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
              <Button>Upload student worksheets</Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[520px]">
              <DialogHeader>
                <DialogTitle>Upload student worksheets</DialogTitle>
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

      <section className="rounded-lg border border-border p-4 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Step 1: Upload Master Key (Required)</h2>
            <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
              <li>1) Dashed/thin outline around EACH question region (includes Q label + answer box)</li>
              <li>2) Circled Q1/Q2… label inside the region</li>
              <li>3) Small solid/thick answer box inside the region</li>
              <li>4) Write the correct answer inside the answer box</li>
            </ul>
          </div>
          <div className="flex items-center gap-2">
            <input
              ref={masterKeyInputRef}
              type="file"
              accept={TEMPLATE_MIME.join(",")}
              className="hidden"
              onChange={(e) => handleMasterKeySelected(e.target.files?.[0])}
            />
            <Button variant="secondary" onClick={openMasterKeyPicker} disabled={masterKeyUploading}>
              {assignment?.template_storage_path ? "Replace Master Key" : "Upload Master Key"}
            </Button>
          </div>
        </div>
        {assignment?.template_storage_path ? (
          <div className="text-sm text-muted-foreground">
            Template detected ✅{" "}
            {assignment?.template_regions_count ? `(${assignment.template_regions_count} questions)` : ""}
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">
            No Master Key yet. Upload one to enable deterministic grading.
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Step 2: Upload Student Worksheets</h2>
        </div>

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
                  No uploads yet. Add files to start OCR and grading.
                </TableCell>
              </TableRow>
            )}
            {uploads.map((upload) => (
              <TableRow key={upload.id}>
                <TableCell className="font-medium">{upload.original_name || "Untitled"}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      variant={baseStatus(upload) === "error" ? "destructive" : "secondary"}
                    >
                      {statusLabel(baseStatus(upload))}
                    </Badge>
                    {reviewState(upload) && (
                      <Badge variant="outline">
                        {statusLabel(reviewState(upload))}
                      </Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button
                      size="sm"
                      onClick={() => openViewer(upload)}
                      disabled={isProcessing(baseStatus(upload))}
                      title={isProcessing(baseStatus(upload)) ? "Processing" : "View submission"}
                    >
                      View
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isProcessing(baseStatus(upload))}
                          aria-label="More actions"
                        >
                          ...
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {baseStatus(upload) === "error" && (
                          <>
                            <DropdownMenuItem onClick={() => handleRetry(upload)}>
                              {retrying[upload.id] ? "Retrying..." : "Retry"}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                          </>
                        )}
                        {upload.graded_pdf_path && (
                          <DropdownMenuItem onClick={() => openOverride(upload)}>
                            Review/Override
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuItem onClick={() => handleDownloadOriginal(upload)}>
                          Download original
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={!upload.graded_pdf_path}
                          onClick={() => handleDownloadMarked(upload)}
                        >
                          Download marked PDF
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-destructive focus:text-destructive"
                          onClick={() => {
                            setDeleteTarget(upload);
                            setDeleteOpen(true);
                          }}
                        >
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        </div>
      </section>

      <Dialog open={viewerOpen} onOpenChange={setViewerOpen}>
        <DialogContent className="sm:max-w-[900px]">
          <DialogHeader>
            <DialogTitle>Submission viewer</DialogTitle>
            <DialogDescription>{viewerTarget?.original_name}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant={viewerTab === "original" ? "default" : "outline"}
              onClick={() => setViewerTab("original")}
            >
              Original
            </Button>
            <Button
              size="sm"
              variant={viewerTab === "marked" ? "default" : "outline"}
              onClick={() => setViewerTab("marked")}
              disabled={!viewerUrls.marked}
            >
              Marked PDF
            </Button>
          </div>
          <div className="space-y-3">
            {viewerLoading && (
              <div className="text-sm text-muted-foreground">Loading document...</div>
            )}
            {!viewerLoading && viewerError && (
              <div className="text-sm text-destructive">{viewerError}</div>
            )}
            {!viewerLoading && !viewerError && viewerTab === "original" && viewerUrls.original && (
              isPdf(viewerTarget) ? (
                <iframe
                  title="Original PDF"
                  className="h-[70vh] w-full rounded-md border"
                  src={viewerUrls.original}
                />
              ) : (
                <img
                  src={viewerUrls.original}
                  alt={viewerTarget?.original_name || "Original"}
                  className="max-h-[70vh] w-full rounded-md object-contain"
                />
              )
            )}
            {!viewerLoading && !viewerError && viewerTab === "marked" && viewerUrls.marked && (
              <iframe
                title="Marked PDF"
                className="h-[70vh] w-full rounded-md border"
                src={viewerUrls.marked}
              />
            )}
            {!viewerLoading && !viewerError && viewerTab === "marked" && !viewerUrls.marked && (
              <div className="text-sm text-muted-foreground">Marked PDF not ready yet.</div>
            )}
            {!viewerLoading && !viewerError && viewerTab === "original" && viewerUrls.original && (
              <a
                className="text-xs text-muted-foreground underline"
                href={viewerUrls.original}
                target="_blank"
                rel="noreferrer"
              >
                Open original in new tab
              </a>
            )}
            {!viewerLoading && !viewerError && viewerTab === "marked" && viewerUrls.marked && (
              <a
                className="text-xs text-muted-foreground underline"
                href={viewerUrls.marked}
                target="_blank"
                rel="noreferrer"
              >
                Open marked PDF in new tab
              </a>
            )}
          </div>
          <DialogFooter className="flex flex-col gap-2 sm:flex-row sm:justify-between">
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => handleDownloadOriginal(viewerTarget)}
                disabled={!viewerUrls.original}
              >
                Download original
              </Button>
              <Button
                variant="outline"
                onClick={() => handleDownloadMarked(viewerTarget)}
                disabled={!viewerUrls.marked}
              >
                Download marked PDF
              </Button>
            </div>
            <Button variant="outline" onClick={() => setViewerOpen(false)}>
              Close
            </Button>
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
