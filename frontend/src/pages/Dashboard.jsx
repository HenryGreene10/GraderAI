import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
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
import { supabase } from "../lib/supabaseClient";

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleDateString();
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
  const [submitting, setSubmitting] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      setEmail(data.user?.email ?? null);
    });
  }, []);

  useEffect(() => {
    if (!dialogOpen) {
      setTitle("");
      setDescription("");
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

  async function handleCreateAssignment() {
    const trimmed = title.trim();
    if (!trimmed) {
      toast({ variant: "destructive", title: "Assignment name is required" });
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

      toast({
        title: "Assignment created",
        description: "Scan the master key to continue.",
      });
      setDialogOpen(false);
      await loadAssignments();
      navigate(`/assignments?assignmentId=${assignmentId}&scan=master_key`);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Create failed",
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

  async function handleDeleteAssignment() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    try {
      const resp = await apiFetch(`/api/assignments/${deleteTarget.id}`, { method: "DELETE" });
      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new Error(text || `Delete failed: ${resp.status}`);
      }
      setAssignments((prev) => prev.filter((row) => row.id !== deleteTarget.id));
      toast({ title: "Assignment deleted" });
      setDeleteOpen(false);
      setDeleteTarget(null);
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Delete failed",
        description: err?.message || "Try again.",
      });
    } finally {
      setDeleting(false);
    }
  }

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
                Name the assignment, then scan the master key.
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
                onClick={handleCreateAssignment}
                disabled={!title.trim() || submitting}
              >
                {submitting ? "Creating..." : "Create assignment"}
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
                  No assignments yet. Create one to start scanning.
                </TableCell>
              </TableRow>
            )}
            {assignments.map((assignment) => (
              <TableRow key={assignment.id}>
                <TableCell className="font-medium">{assignment.title}</TableCell>
                <TableCell>{formatDate(assignment.created_at)}</TableCell>
                <TableCell>{assignment.uploads_count ?? 0}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => navigate(`/assignments?assignmentId=${assignment.id}`)}
                    >
                      Open
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => {
                        setDeleteTarget(assignment);
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

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete assignment “{deleteTarget?.title || "Assignment"}”?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This will remove the assignment and its uploads. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteAssignment} disabled={deleting}>
              {deleting ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
