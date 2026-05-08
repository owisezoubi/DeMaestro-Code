# BOOK_CHANGES.md

Running log of Phase B decisions that change or refine the Phase A book. At the end of Phase B, fold these into the relevant chapters of `DeMaestro.pdf`.

---

## 1. Dual-agent AI engine (replaces single-agent Gemini)

**Affects:** §4.2 (Use of Artificial Intelligence), §5.1 (Artificial Intelligence Technology), §7.6.6 (Dependency on External AI Services), Figure 1, Figure 2.

**Phase A statement:** "DeMaestro uses a single large language model, Google Gemini, as the core AI component."

**Phase B reality:** Two specialized agents. Gemini handles requirements analysis, clarification, summary generation, and blueprint generation. Claude (via the Claude Agent SDK) handles code generation and the iterative test/debug loop inside a Docker sandbox.

**Justification:** The split more faithfully matches the system's title ("Autonomous Multi-Agent System") and uses each model's strengths — Gemini for structured JSON tasks, Claude for agentic code execution. The hand-off contract is the user-approved blueprint JSON, validated with Pydantic at both ends.

**Trade-off:** §7.6.6 must now mention dependency on two external AI services rather than one.

---

## 2. Hardened Docker sandbox for runtime testing

**Affects:** §4.5 (Verification and Source Code Export Mechanism), §7.6.4 (Limited Verification and Testing Capabilities).

**Phase A statement:** "Verification stage focuses on basic structural and integration checks... full functional testing of generated applications is difficult to automate."

**Phase B reality:** Every generated project runs inside an ephemeral, per-project Docker container with hardening flags (`network_mode=none`, `mem_limit=512m`, `cpu_quota=50%`, 5-minute timeout). Claude executes the generated app, reads errors, and iterates until the app boots cleanly.

**Justification:** Lifts the previously scoped-out limitation in §7.6.4. NFR2 ("most generated projects shall run locally without syntax errors") becomes verifiable, not aspirational.

---

## 3. Curated stack menu replaces fixed stack

**Affects:** §4.2 (Use of Artificial Intelligence — "predefined technology stack"), §5.4.2 (Generated Application Database).

**Phase A statement:** "The AI is restricted to a predefined technology stack and expected output formats."

**Phase B reality:** Three supported stacks (`python-sqlite`, `python-postgres`, `node-mongo`). The blueprint phase chooses one based on the user's requirements. A new clarification step covers stack selection (see Stack Selection — Three Cases in the Phase B Architecture Guide).

**Justification:** Honors the "flexible per blueprint" approval given in early Phase B planning while keeping verification tractable. The clarification loop, which Phase A used only for feature ambiguity, now also resolves stack ambiguity.

---

## 4. Backend framework: FastAPI (refines Phase A's "Python")

**Affects:** §5.3.1 (Backend Orchestration Server).

**Phase A statement:** "The backend of the system is implemented using Python."

**Phase B reality:** Specifically FastAPI on Uvicorn, with Pydantic v2 for all request/response and AI artifact schemas.

**Justification:** Async-native (matches long-running AI calls), Pydantic-first (single source of truth for AI schemas and HTTP contracts), and auto-generated OpenAPI document doubles as a verification surface for NFR3 (frontend↔backend route consistency).

---

## 5. Specific tool stack additions

**Affects:** §5.6 (Development and Version Control Tools).

**Phase A statement:** Lists VS Code and Git/GitHub.

**Phase B reality:** Add Sentry (error tracking, free tier), structlog (NFR9 logs), Docker SDK for Python (sandbox lifecycle), PyMuPDF (PDF parsing), TanStack Query (frontend polling), shadcn/ui (Tailwind component library), pre-commit hooks (ruff/black/eslint/prettier).

**Justification:** Concrete realization of the categories committed to in Chapter 5; none of these contradict Phase A.

---

## 6. AI mock mode (for development)

**Affects:** §4.4 (AI Agent Workflow) — minor, optional addition.

**Phase B reality:** A `MOCK_AI=1` env flag short-circuits the Gemini and Claude clients with canned JSON responses. Used during frontend development and tests.

**Justification:** Cost and latency control; no impact on production behavior. Can be mentioned in a dev-tools paragraph or omitted from the book entirely.

---

## 7. Pinned model versions

**Affects:** §5.1 (paragraph on Gemini).

**Phase B reality:** Model versions are pinned in `backend/.env` (e.g., `GEMINI_MODEL=gemini-2.5-pro`, `CLAUDE_MODEL=claude-sonnet-4-6`).

**Justification:** Prevents silent regressions when providers update their default model. Explicit upgrade path.

---

## Diagrams to refresh

- **Figure 1** (workflow) — split the single AI block into two distinct agent blocks with the approval-gate hand-off.
- **Figure 2** (architecture) — split the AI Agent block; show Sandbox Layer; show Cloud Storage as a third Firebase service.
- **Figure 4** (activity diagram) — show stack-selection decision inside the clarification swim lane.

The Phase B Architecture Guide (`DeMaestro_Architecture_Guide.pdf`) already contains these refreshed diagrams; they can be reused directly when updating the Phase A book.

---

## Pending — to be added during the build

These will accumulate as we implement:

- [ ] Final list of generated stack templates (after templates are written).
- [ ] Performance numbers (avg generation time, avg token cost) — populate after running the eval set.
- [ ] SUS evaluation results — to be inserted into Chapter 6.
- [ ] Screenshots of the final UI for §7.5 (Prototype).
- [ ] Updated verification table (§8) reflecting the actual checks implemented.
