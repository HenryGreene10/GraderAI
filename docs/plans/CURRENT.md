# Current Plan (GraderAI) — Scan-Required Pivot

## Why this plan is changing
- Misaligned marks + distorted PDFs come from inconsistent coordinate spaces and hidden resizes.
- A scan-first (phone-as-scanner) flow lets us control capture, rectify once, and treat one artifact as truth.
- We are committing to **SCAN-FIRST + SCAN-REQUIRED** for production (no random photo uploads).

## Policy: Scan-Required
- Capture must go through **QR → mobile scanner page → upload**.
- Teacher convenience is handled by **rapid capture loop (tap-tap)** + optional retake prompts.

## Canonical artifact storage mapping (invariant)
- For scan-first flows, `uploads.normalized_image_path` **is the canonical scan_png**.
- `uploads.normalized_width_px` / `uploads.normalized_height_px` **must match the actual PNG dimensions**.
- OCR / template / grading / overlay / PDF **MUST use normalized_* only** for placement (no other image inputs).
- Naming can change later to `scan_*`, **no schema change in this pivot**.

## Scan session implementation (table-based)
- Add a minimal Supabase table `scan_sessions` with columns:
  `id (uuid), token (random), owner_id, assignment_id, mode ('master_key'|'student'), status, created_at, expires_at, resulting_upload_id (nullable)`.
- TTL: `expires_at` <= 15 minutes; backend rejects expired tokens.
- RLS: owner_id = auth.uid() can create/read their sessions; token-use is validated server-side (no broad access).
- Keep it minimal—no extra fields.

## Scan modes + write targets (explicit)
- **master_key scan** writes:
  - Store canonical PNG under templates at `submissions/{owner}/templates/{assignment}.png`.
  - Update `assignments.template_storage_path` and `template_width_px/height_px` from canonical PNG.
- **student scan** writes:
  - Create uploads row; store canonical `normalized_image_path` + width/height.
  - Trigger OCR + grading pipeline **against canonical PNG**.
- Artifacts generated:
  - `submissions/{owner}/templates/{assignment}.png` (master key)
  - `submissions/{owner}/normalized/{upload}.png` + width/height (student)
  - `graded-pdfs/{owner}/{upload}.pdf` + `overlays/{owner}/{upload}.json`

## Tickets (4–5 total, minimal-risk ordering)

### Ticket 1 — Scan Sessions + QR + Mobile Capture (Scan Required)
Scope
- Implement scan session creation + QR display on desktop.
- Mobile scanner page for `/scan/<token>` with rapid capture loop.
- Upload stores canonical PNG + width/height; links to assignment/upload.
Acceptance checks
- QR opens scanner; scan completes and desktop updates.
- Capture loop allows fast multi-capture loop (each capture = one upload row); optional retake prompt.
Evidence to capture
- Session logs (token, mode, assignment_id, status transitions).
- Stored canonical PNG path + width/height.
Do NOT do
- No alternate upload entry points in production flow.
- No image rectification yet.

### Ticket 2 — Rectify + Quality Gate (Retake UX)
Scope
- Corner detect + perspective warp to produce canonical PNG.
- Blur/edge/contrast checks; require retake if below threshold.
Acceptance checks
- Rectified PNG looks aligned; low-quality scans prompt retake.
Evidence to capture
- Before/after images for 2 scans + quality metrics.
Do NOT do
- No 3rd‑party scanner SDK unless OpenCV path is insufficient.

### Ticket 3 — Wire Canonical PNG Through OCR + Template + Grading
Scope
- OCR input is canonical PNG only.
- Template detection and grading use canonical PNG coordinates only.
Acceptance checks
- OCR/Template overlays align to canonical PNG.
- Mark placement matches expected locations on known scans.
Evidence to capture
- OCR overlay PNG + template overlay PNG on canonical PNG.
Do NOT do
- No new OCR provider or LLM work.
- No fallback to legacy inputs for placement.

### Ticket 4 — Lock PDF Sizing + Overlay Mapping (Hard Invariants)
Scope
- PDF page size MUST be derived from canonical PNG at 300 DPI:
  - `W_pt = W_px * 72 / 300`, `H_pt = H_px * 72 / 300`.
- Background image placed to **exactly fill** that page size.
- Overlay mapping uses the **same DPI conversion**.
- **No clamp/rescale after overlay math; mediabox must equal derived size.**
Acceptance checks
- Mediabox equals derived size; overlays land correctly.
Evidence to capture
- Log: PNG size, mediabox size, overlay assumed size, sx/sy.
Do NOT do
- No PDF post-processing that rescales the page.

### Ticket 5 (Optional) — Deprecate Legacy Paths (After Scan-First is Proven)
Scope
- Keep legacy code for non-scan inputs but bypass it for scan-first placement.
Acceptance checks
- Scan-first path never calls legacy clamp/warp/normalization except Ticket 2 rectify.
Evidence to capture
- Logs showing scan-first bypass of legacy paths.
Do NOT do
- No large refactor or feature removal yet.

## Guardrail: Prevent Legacy Path Leakage
- Scan-first flow MUST NOT call legacy clamp/warp/normalization paths except the single rectify step in Ticket 2.
- If legacy code remains, it must be bypassed for scan-first placement.

## Debug protocol (required)
For any failing upload, record:
- Canonical PNG width/height (px)
- PDF mediabox size (pt)
- Overlay assumed page size (pt)
- sx/sy ratios used for px→pt mapping
- Two sample boxes before mapping + after mapping
- Save debug artifacts (canonical PNG, OCR overlay, marks overlay, marked PDF)
