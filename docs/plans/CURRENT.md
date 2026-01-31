# Current Plan (GraderAI) — Scan-Required, Batch Web Scanner

## Why this plan is changing

• Misaligned marks and distorted PDFs come from inconsistent coordinate spaces,
hidden resizes, and uncontrolled photo uploads.  
• A scan-first (phone-as-scanner) flow lets us control capture, rectify once,
and treat one canonical document artifact as truth.  
• We are committing to **SCAN-FIRST + SCAN-REQUIRED** for production
(no random photo or gallery uploads).

---

## Policy: Scan-Required

• All production capture must go through **QR → mobile scanner page**.  
• The mobile page behaves like a **document scanner**, not a photo uploader.  
• Teacher convenience is handled by:
  – rapid capture loop (tap / auto-capture)  
  – preview + retake gate  
  – batch scanning per student (no per-page uploads)

---

## Canonical artifact (NEW invariant)

**Canonical grading input = one multi-page PDF per student submission.**

• Each PDF page originates from a rectified scan page.  
• OCR, grading, overlays, and marked PDFs are **page-indexed** against this PDF.  
• No downstream system consumes raw photos or unrectified images.

Internal details:
• Rectified page images may exist transiently or as debug artifacts.  
• Placement math always references:
  – page index  
  – page width/height derived from the rectified scan  

---

## Scan session implementation (table-based)

• Add minimal Supabase table `scan_sessions`:
  - id (uuid)  
  - token (random)  
  - owner_id  
  - assignment_id  
  - status  
  - created_at  
  - expires_at  

• TTL: `expires_at ≤ 15 minutes`  
• RLS:
  - owner_id = auth.uid() can create/read sessions  
  - token use validated server-side  
• Scan session may produce **multiple student submissions**.

---

## Scan workflow (QR → batch → per student)

### Mobile scanner behavior

• Live camera with document frame overlay  
• Capture → **rectify + preview** → Use / Retake  
• Accepted pages accumulate in a **current student packet**  
• Teacher taps **Finish student** to close the packet  
• Packet is converted into **one multi-page PDF** and uploaded once  
• Memory is cleared; scanning continues for next student  

No individual photo uploads. No per-page upload rows.

---

## Storage & write targets

### Student scan output

• For each finished student packet:
  - Create **one submission row**
  - Store canonical PDF at:
    `submissions/{owner}/{submission}.pdf`
  - Store metadata:
    – page_count
    – per-page width/height (from rectified pages)

### Artifacts generated

• Canonical input:
  - `submissions/{owner}/{submission}.pdf`

• Derived:
  - `graded-pdfs/{owner}/{submission}.pdf`
  - `overlays/{owner}/{submission}.json` (page-indexed)

---

## Tickets (minimal-risk ordering)

### Ticket 0 — Overlay styling scale (readability)
_(unchanged, but must be completed first)_

---

### Ticket 1 — Batch Web Scanner (QR → Rectify → Student Packets)

Scope  
• Scan session creation + QR display on desktop  
• Mobile scanner page `/scan/<token>`  
• OpenCV.js rectification (client-side)  
• Preview + retake gate  
• Page tray per student  
• “Finish student” → build multi-page PDF (client) → upload once  

Acceptance checks  
• Scan 5–10 pages rapidly into one student PDF  
• No per-page uploads created  
• Session continues cleanly to next student  

Do NOT do  
• No OCR / grading  
• No legacy upload paths  

---

### Ticket 2 — Quality Gate + Scan Enforcement

Scope  
• Blur / edge / confidence checks  
• Force retake if scan quality is insufficient  
• Backend rejects non-scan uploads in production mode  

Acceptance checks  
• Bad scans cannot proceed  
• Scan-required invariant enforced  

---

### Ticket 3 — Page-Aware OCR + Grading

Scope  
• Render PDF pages to images  
• OCR per page  
• Grading operates per page index  
• Overlay JSON becomes `{ pages: [...] }`

Acceptance checks  
• OCR + overlays align page-by-page  
• Known scans render correct marks  

---

### Ticket 4 — Lock PDF Sizing + Overlay Mapping (Hard Invariants)

Scope  
• PDF page size derived from rectified scan page size  
• No rescaling after placement math  
• Overlay mapping is page-indexed  

---

### Ticket 5 (Optional) — Deprecate Legacy Paths

Unchanged: keep legacy paths for non-scan inputs, but bypass entirely for scan sessions.

---

## Guardrail: Prevent Legacy Path Leakage

• Scan-first flow MUST NOT call legacy photo or normalization paths.  
• If legacy code remains, it must be bypassed entirely for scan sessions.

---

## Local dev networking (phone scan, no LAN backend)

• Start backend (loopback only):
  uvicorn backend.app:app --host 127.0.0.1 --port 8000
• Start Vite (LAN reachable):
  npm --prefix frontend run dev -- --host 0.0.0.0 --port 5173
• Set `VITE_PUBLIC_BASE_URL=http://<LAN_IP>:5173` in `frontend/.env.local`.
• Phone opens `http://<LAN_IP>:5173/scan/<token>`.
• Vite proxies `/api/*` to `http://127.0.0.1:8000`, so backend stays off-LAN.

---

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
