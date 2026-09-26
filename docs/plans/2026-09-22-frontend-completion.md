# Frontend Completion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use the repository API contracts and complete each vertical slice before moving to the next.

**Goal:** Provide a role-aware React interface for every user-facing backend contour that currently exists.

**Architecture:** Keep `App.tsx` as shell/router only and move feature state into focused drawers and panels. API functions remain centralized in `api.ts`; feature components consume explicit role and identifier props and never infer permissions beyond hiding commands that the backend will reject.

**Tech Stack:** React 19, TypeScript, Vite, plain CSS, lucide-react, FastAPI REST contracts, Playwright smoke checks.

---

### Task 1: Shared shell and contracts
- Extend API client for auth, notifications, support, exports, admin users and operational resources.
- Add reusable drawer states, client pagination/filtering and accessible empty/error/loading states.
- Verify with `npm.cmd run build`.

### Task 2: Support workflow
- Add creator ticket creation and role-aware ticket detail.
- Add messages, assignment to self, status transitions and recovery decisions.
- Verify creator and staff workflows with mocked Playwright routes.

### Task 3: Export workflow
- Add export creation with type-specific date filters and XLSX/CSV selection.
- Add job details, failed-job feedback, progress refresh and ready-file download.
- Verify every role allowed by the backend can create and inspect jobs.

### Task 4: Administration
- Add user details, role changes and block/unblock commands.
- Add product catalog management and staff read-only catalog.
- Add security-event details, billing controls and legal document administration where routes exist.

### Task 5: Account lifecycle
- Add registration with current legal-document acceptance.
- Add email verification, password reset request and reset confirmation flows.
- Add account status, legal acceptance and deletion request UI using existing endpoints.

### Task 6: Notifications and navigation
- Connect unread count, notification panel, read-one/read-all and action navigation.
- Connect Settings and Help buttons to real panels/workflows.
- Add 403/404 and expired-session handling without leaking backend details.

### Task 7: List usability and entity details
- Add working status filters, sort and client pagination for loaded collections.
- Connect detail views for profiles, publications, readings and video cards.
- Preserve role constraints and responsive behavior.

### Task 8: Verification and review
- Run TypeScript/Vite production build and `git diff --check`.
- Run Playwright at desktop and mobile widths for blogger, moderator, manager, finance and admin.
- Review correctness, readability, architecture, security and performance; fix required findings.
