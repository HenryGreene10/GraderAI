# Current Plan (GraderAI) — Two-Phase Execution

## Why this plan is changing

We have a working DEV-only workflow that produces a marked PDF end-to-end without the mobile scanner.  
Grading accuracy is currently poor (e.g., 1/9 scores), and the root cause is unclear
across OCR quality, answer-key parsing, or grading logic.

This plan prioritizes **grading truth** first, then returns to scan-required capture.
This is a reprioritization, not a reversal of the scan-first production vision.

---

# Phase A — Grading Truth (Immediate Priority)

## Purpose
Ensure grading is accurate, deterministic, and explainable on clean PDF inputs
using the DEV-only upload path. Prove core product value: time savings and a printable
graded PDF.

## Goals
• De-risk OCR → grading → overlay correctness  
• Make accuracy measurable and debuggable  
• Validate output on a small golden set

## Canonical Pipeline Rule (Required)
There is exactly one normalized grading pipeline.

• All entry points (DEV upload, Scan Students, legacy `/api/grade`, manual re-run) must route into the same OCR → template grading → overlay pipeline.  
• DEV mode can only change file acquisition, never grading path selection.  
• Template-backed assignments must use template grading once master key regions exist (no silent LLM/single-pass fallback).  
• Grading path metadata must be inspectable per submission (`grade_json.pipeline` + template frame source fields).

## Tickets

### Ticket A1 — DEV-only Desktop Upload Path (DONE)
Allow PDF/image upload for answer keys and student worksheets.  
Bypass mobile scanner entirely.  
Clearly marked as DEV ONLY.

### Ticket A2 — OCR Visibility & Diagnostics
Persist OCR text for:
• answer key  
• student submission  
Expose OCR output via logs, admin UI, or a debug endpoint.

### Ticket A3 — Structured Answer Key Extraction (DONE)
Use LLM only to extract answers into strict JSON:
```
{ "question_number": "expected_answer" }
```
No grading in this step.

### Ticket A4 — Structured Student Answer Extraction (DONE)
Extract student answers into the same JSON schema.  
Ignore scratch work where possible.

### Ticket A5 — Template Regions + Mark Placement Recovery (PATCH PLAN v2) (IN PROGRESS)
Goal: fix incorrect question counts, weak region answer reads, and missing/misaligned ✓/✗ marks.

Execution rule:
• Implement exactly one patch step at a time.  
• After each step, pause for manual test results before moving to the next step.

Patch sequence:
1. Patch A5.1 — Remove hard caps and hardcoded `Q1..Q9` assumptions.
   Acceptance:
   • Region count in assignment metadata equals the true detected count (not capped at 9).  
   • Grading iterates actual template qids only.
2. Patch A5.2 — Enforce one coordinate contract end-to-end.
   Acceptance:
   • Overlay marks are rendered in the intended location on page 1.  
   • No double conversion between px and PDF points.
3. Patch A5.3 — Make template grading alignment-first.
   Acceptance:
   • For template-backed assignments, aligned page/frame is used before region extraction when available.  
   • Fallback path is explicit and flagged.
   • Debug artifacts include per-question crops from the exact aligned/scaled OCR frame (`debug/qid_Q3_crop.png`, etc.).
4. Patch A5.4 — Place visible marks for low-confidence items.
   Acceptance:
   • Low-confidence questions still get a visible review marker/note, not silent omission.  
   • `needs_review` stays true when confidence is low.
5. Patch A5.5 — Support multi-page overlays.
   Acceptance:
   • Mark placement works on pages beyond page 1.  
   • Overlay JSON and PDF flattening preserve page index.
6. Patch A5.6 — Fix scan upload orchestration.
   Acceptance:
   • Student scan upload reliably triggers OCR + grading lifecycle (or explicit queued state).  
   • No scan uploads remain stranded at `scanned` without grade progression.
7. Patch A5.7 — Improve scoring tolerance/parsing.
   Acceptance:
   • Fewer false `needs_review` outcomes for legible quotient/remainder answers.  
   • Incorrect vs needs-review distinction is consistent.
8. Patch A5.8 — Add regression coverage for the above.
   Acceptance:
   • Automated tests cover: <9, =9, >9 questions, multi-page PDFs, low-confidence mark behavior, and coordinate mapping.
9. Patch A5.9 — Standardize normalized sizing units.
   Acceptance:
   • `normalized_width_px` / `normalized_height_px` are stored as true pixel dimensions.  
   • OCR geometries that arrive in non-pixel units (inch/cm/etc.) are converted to px before grading/overlay.  
   • DEV upload and scan upload produce consistent overlay placement under the same coordinate contract.

Execution status (current branch):
• A5.1 implemented  
• A5.2 implemented  
• A5.3 implemented  
• A5.4 implemented  
• A5.5 implemented  
• A5.6 implemented  
• A5.7 implemented  
• A5.8 implemented
• A5.9 implemented (unit-safe normalized sizing)

### Ticket A6 — Golden Set Validation (after A5.1–A5.8)
Ensure overlays align correctly and grading is stable on known worksheets.  
Produce printable, readable marked PDFs.  
Validate on a golden set (at least 3 worksheets, including multi-page).

Current hardening focus:
• A6.1 mark readability (larger/thicker visible ✓/✗)  
• A6.2 overlay completeness (every graded item must emit a visible ✓/✗/REVIEW mark, with fallback notes when anchor placement fails)  
• A6.3 straggler reduction (page-specific size mapping + aligned/scaled frame dimensions as overlay normalization source)

### Ticket A7 — Master Key = Truth + One Pipeline Lockdown (PATCH PLAN v3) (NEXT)
Goal: make question count and mark emission deterministic so template-backed grading cannot drift.

Execution rule:
• Implement exactly one step at a time, in order.  
• After each step, pause for manual verification before moving to the next.

Deliverables mapping:
• D1 frozen manifest schema + versioning → Steps 1, 2, 7  
• D2 unified ingestion entry points → Step 5  
• D3 strict template grading contract (no non-template leakage) → Steps 3, 4  
• D4 overlay integrity gates → Step 6  
• D5 stale-output invalidation → Step 8  
• D6 golden-set invariant tests → Step 9

Ordered patch sequence:
1. Define canonical template manifest contract (authoritative fields, required ids, page + box semantics). `[implemented]`
2. Freeze manifest at template approval and persist immutable versioned payload (no downstream qid rewriting/reordering). `[implemented]`
3. Enforce strict template grading contract: grade only manifest qids, no heuristic question discovery in template mode. `[implemented]`
4. Enforce single anchor outcome per manifest question with explicit degraded-mode marker when placement fails. `[implemented]`
5. Unify master-key ingestion entry points (`/assignments/{id}/template` and scan master-key upload) onto the same normalization + extraction + approval pipeline. `[implemented]`
6. Add overlay integrity gates (one visible mark per graded item; no unknown qids; fail closed to review when violated). `[implemented]`
7. Add manifest/version stamping and runtime compatibility checks (`template_manifest_version`, `template_version_used`). `[pending]`
8. Invalidate stale graded artifacts on template/version change (or force deterministic regrade before serving). `[pending]`
9. Add golden-set invariant tests: exact question count, one-mark-per-question, no extras, bounded placement tolerance, deterministic path. `[pending]`
10. Add operator-facing degraded-mode visibility and debug trace fields for frame source + fallback reason. `[pending]`

Status snapshot for this track:
• A6.1 implemented  
• A6.2 implemented  
• A6.3 implemented  
• A7.1 implemented (contract module + validation tests)
• A7.2 implemented (approved manifest persisted + locked in template payload)
• A7.3 implemented (template grading keyed strictly to manifest qids)
• A7.4 implemented (manifest mark-count integrity degrades to review)
• A7.5 implemented (shared master-key approval pipeline used by assignment template upload + scan master-key upload)
• A7.6 implemented (template overlay integrity gates: mark-count + qid-set validation with fail-closed-to-review on violations)
• A7.7–A7.10 pending
• Execution pause: waiting for manual verification before starting A7.7

## Acceptance Criteria (Phase A)
• Clean PDFs grade correctly (near-perfect scores).  
• One can upload a key + students and download a reliable graded PDF.

---

# Phase B — Scan-Required Capture (Production Hardening)

## Purpose
Reintroduce scan-required mobile capture once grading truth is established.
Scan improvements should enhance OCR quality, but **scan correctness does not fix grading logic**.

## Scope (preserve scan-first vision)
• QR → mobile scanner flow  
• Rectification + preview + retake  
• Batch scanning per student  
• Canonical multi-page PDF per submission  
• Page-aware OCR and overlays  
• Quality gates (blur / edge detection)

## Policy: Scan-Required
• All production capture must go through **QR → mobile scanner page**.  
• The mobile page behaves like a **document scanner**, not a photo uploader.  
• Teacher convenience is handled by:
  – rapid capture loop (tap / auto-capture)  
  – preview + retake gate  
  – batch scanning per student (no per-page uploads)

## Canonical artifact (Invariant)
**Canonical grading input = one multi-page PDF per student submission.**

• Each PDF page originates from a rectified scan page.  
• OCR, grading, overlays, and marked PDFs are **page-indexed** against this PDF.  
• No downstream system consumes raw photos or unrectified images.

Internal details:
• Rectified page images may exist transiently or as debug artifacts.  
• Placement math always references:
  – page index  
  – page width/height derived from the rectified scan

## Scan session implementation (table-based)
• Supabase table `scan_sessions`:
  – id (uuid)  
  – token (random)  
  – owner_id  
  – assignment_id  
  – status  
  – created_at  
  – expires_at  

• TTL: `expires_at ≤ 15 minutes`  
• RLS:
  – owner_id = auth.uid() can create/read sessions  
  – token use validated server-side  
• Scan session may produce **multiple student submissions**.

## Scan workflow (QR → batch → per student)
### Mobile scanner behavior
• Live camera with document frame overlay  
• Capture → **rectify + preview** → Use / Retake  
• Accepted pages accumulate in a **current student packet**  
• Teacher taps **Finish student** to close the packet  
• Packet is converted into **one multi-page PDF** and uploaded once  
• Memory is cleared; scanning continues for next student

No individual photo uploads. No per-page upload rows.

## Storage & write targets
### Student scan output
• For each finished student packet:
  – Create **one submission row**  
  – Store canonical PDF at:
    `submissions/{owner}/{submission}.pdf`  
  – Store metadata:
    – page_count  
    – per-page width/height (from rectified pages)

### Artifacts generated
• Canonical input:
  – `submissions/{owner}/{submission}.pdf`
• Derived:
  – `graded-pdfs/{owner}/{submission}.pdf`  
  – `overlays/{owner}/{submission}.json` (page-indexed)

## Guardrail: Prevent Legacy Path Leakage
• Scan-first flow MUST NOT call legacy photo or normalization paths.  
• If legacy code remains, it must be bypassed entirely for scan sessions.  
• Legacy upload paths remain DEV-only or deprecated during Phase B.

---

## Local dev networking (phone scan, no LAN backend)
• Start backend (loopback only):
  uvicorn backend.app:app --host 127.0.0.1 --port 8000  
• Start Vite (LAN reachable):
  npm --prefix frontend run dev -- --host 0.0.0.0 --port 5173  
• Set `VITE_PUBLIC_BASE_URL=http://<LAN_IP>:5173` in `frontend/.env.local`.  
• Phone opens `http://<LAN_IP>:5173/scan/<token>`.  
• Vite proxies `/api/*` to `http://127.0.0.1:8000`, so backend stays off-LAN.

## Debug protocol (page-aware)
For any failing submission, record:
• Page count  
• For one sample page:
  – rectified width/height (px)  
  – PDF mediabox size (pt)  
  – overlay assumed size  
  – sx/sy mapping ratios  
• Save artifacts:
  – rectified page image  
  – overlay JSON  
  – marked multi-page PDF
