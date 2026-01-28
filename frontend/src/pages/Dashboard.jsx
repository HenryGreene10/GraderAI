import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "../lib/apiBase";
import { supabase } from "../lib/supabaseClient";

const ACCEPTED_MIME = ["image/png", "image/jpeg", "application/pdf"];
const ACCEPTED_EXT = [".png", ".jpg", ".jpeg", ".pdf"];

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleDateString();
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(size < 10 ? 1 : 0)} ${units[idx]}`;
}

function isAllowedFile(file) {
  if (!file) return false;
  if (ACCEPTED_MIME.includes(file.type)) return true;
  const name = String(file.name || "").toLowerCase();
  return ACCEPTED_EXT.some((ext) => name.endsWith(ext));
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [email, setEmail] = useState(null);
  const [assignments, setAssignments] = useState([]);
  const [loading, setLoading] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef(null);

  const fileCount = useMemo(() => files.length, [files]);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      setEmail(data.user?.email ?? null);
    });
  }, []);

  useEffect(() => {
    if (!dialogOpen) {
      setTitle("");
      setDescription("");
      setFiles([]);
      setSubmitting(false);
    }
  }, [dialogOpen]);

  async function loadAssignments() {
    setLoading(true);
    try {
      const resp = await apiFetch("/api/assignments");
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Failed: ${resp.status}`);
      }
      const data = await resp.json();
      setAssignments(data.assignments || []);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Failed to load assignments",
        description: err?.message || "Try again.",
      });
      setAssignments([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAssignments();
  }, []);

  const addFiles = (incoming) => {
    const list = Array.from(incoming || []);
    if (!list.length) return;
    const rejected = list.filter((file) => !isAllowedFile(file));
    if (rejected.length) {
      toast({
        variant: "destructive",
        title: "Unsupported file type",
        description: "Answer key must be a PNG, JPG, or PDF.",
      });
    }
    const accepted = list.filter(isAllowedFile);
    if (!accepted.length) return;
    setFiles([accepted[0]]);
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

  async function handleCreateAndUpload() {
    const trimmed = title.trim();
    if (!trimmed) {
      toast({ variant: "destructive", title: "Assignment name is required" });
      return;
    }
    if (files.length === 0) {
      toast({ variant: "destructive", title: "Add the answer key file" });
      return;
    }

    setSubmitting(true);
    try {
      const createResp = await apiFetch("/api/assignments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: trimmed,
          description: description.trim() || null,
        }),
      });
      if (!createResp.ok) {
        const text = await createResp.text().catch(() => "");
        throw new Error(text || `Create failed: ${createResp.status}`);
      }
      const createData = await createResp.json();
      const assignmentId = createData.assignment?.id || createData.id;
      if (!assignmentId) {
        throw new Error("Missing assignment id from server");
      }

      const formData = new FormData();
      formData.append("file", files[0]);

      const uploadResp = await apiFetch(`/api/assignments/${assignmentId}/template`, {
        method: "POST",
        body: formData,
      });
      if (!uploadResp.ok) {
        const text = await uploadResp.text().catch(() => "");
        throw new Error(text || `Upload failed: ${uploadResp.status}`);
      }

      toast({
        title: "Assignment created",
        description: "Answer key uploaded.",
      });
      setDialogOpen(false);
      await loadAssignments();
      navigate(`/assignments?assignmentId=${assignmentId}`);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Create & upload failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setSubmitting(false);
    }
  }

  const signOut = async () => {
    await supabase.auth.signOut();
    window.location.href = "/auth";
  };

  return (
    <div className="p-6 space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Assignments</h1>
          <p className="text-sm text-muted-foreground">
            Manage uploads by assignment folder.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {email && <span className="text-sm text-muted-foreground">{email}</span>}
          <Button variant="outline" onClick={signOut}>Sign out</Button>
        </div>
      </header>

      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          {loading ? "Loading assignments..." : `${assignments.length} assignment${assignments.length === 1 ? "" : "s"}`}
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button>Create assignment</Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[520px]">
            <DialogHeader>
              <DialogTitle>Create assignment</DialogTitle>
              <DialogDescription>
                Name the assignment, then upload the answer key.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Assignment name</label>
                <input
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  placeholder="Homework 3"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">Description (optional)</label>
                <textarea
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  placeholder="Short notes for your own reference"
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>

              {title.trim() ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <label className="text-sm font-medium">Upload answer key</label>
                    <Badge variant="outline">Step 1 of 2: Answer Key</Badge>
                  </div>
                  <div className="flex items-center gap-3">
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept={ACCEPTED_MIME.join(",")}
                      className="hidden"
                      onChange={(e) => addFiles(e.target.files)}
                    />
                    <Button variant="secondary" type="button" onClick={openPicker}>
                      Add file
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      Upload the answer key (PNG, JPG, or PDF). Student worksheets are uploaded after creation.
                    </span>
                  </div>

                  {fileCount > 0 && (
                    <div className="max-h-48 overflow-auto rounded-md border border-border p-2">
                      <div className="space-y-2">
                        {files.map((file, idx) => (
                          <div key={`${file.name}-${file.size}`} className="flex items-center justify-between gap-2 text-sm">
                            <div className="truncate">
                              {file.name} <span className="text-muted-foreground">({formatSize(file.size)})</span>
                            </div>
                            <Button
                              size="sm"
                              variant="ghost"
                              type="button"
                              onClick={() => removeFileAt(idx)}
                            >
                              Remove
                            </Button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
                  Enter a name to select the answer key.
                </div>
              )}
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                variant="outline"
                type="button"
                onClick={() => setDialogOpen(false)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleCreateAndUpload}
                disabled={!title.trim() || files.length === 0 || submitting}
              >
                {submitting ? "Uploading..." : "Create & Upload Answer Key"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>#Uploads</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {assignments.length === 0 && !loading && (
              <TableRow>
                <TableCell colSpan={4} className="text-center text-muted-foreground">
                  No assignments yet. Create one to start uploading.
                </TableCell>
              </TableRow>
            )}
            {assignments.map((assignment) => (
              <TableRow key={assignment.id}>
                <TableCell className="font-medium">{assignment.title}</TableCell>
                <TableCell>{formatDate(assignment.created_at)}</TableCell>
                <TableCell>{assignment.uploads_count ?? 0}</TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(`/assignments?assignmentId=${assignment.id}`)}
                  >
                    Open
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
