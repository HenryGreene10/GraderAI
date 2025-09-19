# AGENTS.md — Repository Guidelines for GraderAI

This file defines conventions and rules for Codex agents working in this repo.  
Follow these instructions when generating or modifying code.

---

## Project Structure & Module Organization
- `backend/`: FastAPI service implementing the grading pipeline (OCR → parse → grade → PDF).
  - Entrypoint: `backend/app.py`
  - Submodules:
    - `services/ocr.py` → HandwritingOCR helpers
    - `services/grader.py` → LLM/rubric grading logic
    - `services/report.py` → PDF generation + overlay flattening
    - `models/` → Pydantic schemas for submissions, grades, rubrics
- `frontend/`: React + Vite app with Supabase auth and grading dashboard.
  - Key files:
    - `src/lib/supabaseClient.js`
    - `src/lib/ocr.js`
    - `src/pages/Dashboard.jsx`
    - `src/pages/AssignmentsPage.jsx`
    - `src/components/FileRow.jsx`
- Do not edit build artifacts (`frontend/dist/`).

---

## Build, Test, and Development Commands
- Backend setup:
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

- Backend dev:
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

- Frontend install/dev/build:
npm --prefix frontend install
npm --prefix frontend run dev
npm --prefix frontend run build

---

## Coding Style & Naming Conventions
- **Python**: 4 spaces; `snake_case` for functions/variables; `PascalCase` for classes; constants in `UPPER_SNAKE`.
- **React/JS**: Follow ESLint (`frontend/eslint.config.js`). Components in `PascalCase`, functions/vars in `camelCase`.
- **Filenames**: Python modules use `snake_case`; React components match component name (e.g. `FileRow.jsx`).

---

## Grading Pipeline Rules
- `/grade` endpoint executes pipeline in order:
1. **OCR**: extract text + bounding boxes + confidence.
2. **Parse**: classify question type (`numeric`, `MCQ`, `short_answer`, `show_work`).
3. **Auto-key generation**:
   - Numeric/Algebra → Interpreter tool (canonical numeric answer).
   - MCQ → solution mapped to option.
   - Short Answer → LLM generates reference answer + must-include elements.
4. **Grading**: per-question score, criteria, rationale, confidence flag.
5. **Report**: generate overlay JSON (✓/✗ stamps, score bubbles, margin notes, highlights for low-confidence Qs).
6. **Flatten**: overlay + original PDF → final print-ready graded PDF.
- All jobs must stamp `rubric_version` and `prompt_version`.
- Low confidence (OCR <0.85, solver disagreement, LLM uncertain) → mark `"needs_review"` but still produce output.

---

## Output Artifacts
- **Overlay JSON**: editable marks (check, bubble, notes, highlights).
- **Final Graded PDF**: flattened, immutable, stored in `graded-pdfs/{auth.uid()}/{submissionId}.pdf`.
- **Grade JSON**: per-question scores, rationale, criteria, confidence.
- **Rubric JSON**: generated automatically, versioned, stored with each assignment.

---

## Teacher Overrides
- Teachers never upload rubrics.
- Teachers may override scores/rationales via UI.
- Overrides are stored in `overrides` table, with `who/when/what`.
- Regenerating a PDF must merge overrides with auto-grading.

---

## Testing Guidelines
- **Backend**: pytest under `backend/tests/` (files `test_*.py`).
- Unit test OCR parsing, grading functions, and PDF generation.
- Mock external APIs (Supabase, OCR, LLM).
- **Frontend**: Vitest + React Testing Library under `frontend/tests/`.
- Test upload → status → report download flow.
- Tests must be fast, deterministic, no live API calls.

---

## Commit & Pull Request Guidelines
- Commits: short, imperative subjects with optional scope prefixes (`backend:`, `frontend:`).
- PRs: clear description, screenshots if UI, and steps to validate locally.
- Keep PRs small and focused.

---

## Security & Configuration Tips
- **Backend env vars**:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `HANDWRITINGOCR_API_KEY`
- `CORS_ALLOW_ORIGINS`
- **Frontend env vars (Vite)**:
- `VITE_BACKEND_URL`
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`
- Never commit secrets (beyond public anon keys).
- Supabase storage: use bucket-relative keys (no leading slash).
- Default retention: auto-delete submissions + PDFs after 30 days.

---

## Special Notes for Codex
- Always reference `PROJECT_BRIEF.md` for pipeline details.
- Prefer **unified diffs** when suggesting edits.
- Only touch relevant modules; do not edit build artifacts or `.env`.
- Use `gpt-4o-mini` for quick iterations, `gpt-4o` only for heavy refactors.
- Keep grading deterministic (LLM temp 0–0.2).