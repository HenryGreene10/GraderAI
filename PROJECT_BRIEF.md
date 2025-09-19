# Project Brief — GraderAI MVP

## 🎯 Goal
Build a web platform for teachers to **auto-grade handwritten or typed worksheets** with minimal setup. The system should:
- Accept PDF/image uploads.
- Run OCR → parse questions → auto-generate answer keys → auto-grade responses (numeric, MCQ, short answer).
- Export a **print-ready PDF** with auto-marks (✓/✗, score bubbles, comments).
- Let teachers override/edit grades and regenerate the final PDF.
- Stay lightweight, privacy-conscious, and compliant (FERPA/COPPA).

---

## 🛠 Tech Stack
- **Frontend**: React (Vite)
  - Auth: Supabase (custom email/password form).
  - UI: Dashboard (upload, status, download), AssignmentsPage, file manager (rename/delete).
  - Overlay editor: optional freehand/highlight/stamp tools for teacher annotations.

- **Backend**: Python FastAPI
  - Endpoints:
    - `POST /assignments` → create rubric/version metadata
    - `POST /uploads` → register new submission
    - `POST /grade?submission_id=...` → enqueue grading pipeline
    - `GET /status/{id}` → check job state/progress
    - `GET /report/{id}.pdf` → return flattened, print-ready PDF
    - `POST /override` → apply teacher corrections, regenerate PDF

- **DB/Storage**: Supabase (Postgres + Storage)
  - Buckets:
    - `submissions/` (private, {auth.uid()}/…)
    - `graded-pdfs/` (private, {auth.uid()}/…)
  - Tables:
    - `assignments` (rubric JSON, versions)
    - `uploads` (metadata, file refs)
    - `jobs` (status, timestamps, confidence flags)
    - `grades` (auto results per Q)
    - `overrides` (teacher changes, audit log)

- **OCR**: HandwritingOCR API (pluggable later).

- **LLM**: Mistral/Llama (via OpenRouter), temp=0–0.2 for deterministic grading.

- **Interpreter**: sandboxed Python (future step) for math/code questions (currently optional).

- **PDF generation**: ReportLab/WeasyPrint.

- **Privacy**: pseudonymized student IDs, default 30-day retention, export on demand.

---

## 🔄 Pipeline (End-to-End)

### 1. Upload & Storage
- Teacher uploads worksheet → stored in `submissions/{auth.uid()}/{assignmentId}/{fileId}.pdf`.
- Metadata written to `uploads` table.

### 2. OCR & Parsing
- OCR engine extracts text + bounding boxes.
- Parser identifies questions and answers.
- Each Q tagged as `numeric`, `MCQ`, `short_answer`, or `show_work`.

### 3. Auto-Key Generation
- **Numeric/Algebra:** solved with Interpreter tool (canonical numeric answer).
- **MCQ:** solved and mapped to correct choice.
- **Short-Answer:** LLM produces reference answer + “must-include elements”.
- Confidence scores logged (solver agreement, OCR clarity).

### 4. Auto-Grading
- Compare student answers to auto-keys.
  - Numeric → tolerance + units.
  - MCQ → match.
  - Short Answer → rubric match vs expected elements.
- Output: per-criterion scores, rationale, confidence.

### 5. Auto-Markup → PDF
- Generate overlay JSON:
  - ✓ / ✗ stamps on answers.
  - “3/5” score bubble per question.
  - Margin notes (LLM rationale).
  - Yellow highlight for “low confidence” Qs.
- Flatten overlays + original pages → final print-ready PDF.
- Save in `graded-pdfs/{auth.uid()}/{submissionId}.pdf`.

### 6. Teacher Review (optional)
- In dashboard, teacher sees overlays (✓/✗, comments, scores).
- Can freehand annotate or edit scores/rationales.
- Overrides stored in `overrides` table.
- “Regenerate PDF” → backend rebuilds final PDF with both auto + manual marks.

### 7. Export & Audit
- Final PDF is immutable snapshot with:
  - Student ID, assignment, rubric version, prompt version, grader (auto/teacher), timestamp.
- All overrides are audit-logged.
- Teacher can download/share/print.

---

## 📑 Output Files

1. **Final Graded PDF (flattened)**
   - Auto + manual marks visible (print-ready).
   - Last page summary: totals, rubric version, teacher notes.

2. **Overlay JSON (editable)**
   ```json
   {
     "page": 1,
     "marks": [
       {"tool": "check", "coords": [120, 340]},
       {"tool": "bubble", "coords": [200, 500], "text": "3/5"},
       {"tool": "note", "coords": [300, 450], "text": "Missed +2 intercept"}
     ]
   }