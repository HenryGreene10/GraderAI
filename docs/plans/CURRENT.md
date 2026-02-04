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

### Ticket A3 — Structured Answer Key Extraction
Use LLM only to extract answers into strict JSON:
```
{ "question_number": "expected_answer" }
```
No grading in this step.

### Ticket A4 — Structured Student Answer Extraction
Extract student answers into the same JSON schema.  
Ignore scratch work where possible.

### Ticket A5 — Deterministic Scoring Engine
Compare key JSON vs student JSON in code.  
Support numeric tolerance, formatting variants, and equivalents.  
Produce per-question correctness + score.

### Ticket A6 — Overlay + Marked PDF Validation
Ensure overlays align correctly.  
Produce printable, readable marked PDFs.  
Validate on a small golden set (2–3 worksheets with known answers).

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
