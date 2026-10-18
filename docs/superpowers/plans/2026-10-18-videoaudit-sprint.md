# Videoaudit implementation plan

**Goal:** Implement the user's approved specification without inventing model capability or altering the jury contract.

**Spec:** User attachment `c12a7bd8-6659-4915-979f-d6e81a279ee0/Вставленный текст.txt` and ForteBank case; conversation contains the approved visual references.

**Architecture:** Existing FastAPI worker and frozen encoder remain. Optional point-localization candidate is independent of binary verdict. Protected review/compliance metadata carries progress and human-confirmed rule linkage; jury JSON remains unchanged. Vanilla frontend receives locale/theme settings and a bounded interactive hero.

**Constraints:** No cloud video processing, biometric identification, test tuning, source-label edits, push/merge/deployment. Unknown action types remain unknown. Reports remain preliminary. Preserve baseline artifacts and uncommitted user-approved UI. In-place branch `feat/videoaudit-sprint` preserves the explicitly requested installation and private weights.

## Tasks and interfaces

- [x] Baseline snapshot and isolated runtime: `local/audit/sprint-baseline-2026-10-18.json`, separate `.venv-isolated`, dependency check, model readiness; preserve `.venv` for rollback.
- [x] Measured progress: `progress.py`, `features.py`, `cli.py`, `worker.py`, protected `/review`; `{stage,completed,total,percent,scope:'stage',unit,updated_at}`. Tests: invalid/nonfinite counters, throttled writes, failure not 100%, completion only after stored result, exact jury contract.
- [x] Compliance metadata: `compliance.py`, `/jobs/{id}/compliance`; `events` carry status, observed fact, safe registry rule and provenance. Tests: human clear/applicable confirmation, wrong source, proposed vs confirmed, overlapping event, unsafe source links, auth.
- [x] Point localizer experiment: `point_localization.py`, isolated CLI/artifacts. Train/CV/validation selection before frozen exploratory test evaluation. Integrate only optional config `point_localizer='point-localizer.npz'`; validate encoder/hash and bind provenance. X-13 is a train diagnostic, never a blind metric.
- [x] Event review table and multilingual immutable PDFs: strict CSV import/export and trusted partition derivation; `report_files(..., language='ru')`. Tests: incomplete labels cannot train, forged provenance rejected, all locales and original evidence hashes.
- [x] Frontend: exact verdict from API, genuine stage percentage, human rule panel, no misleading zero marker, concise wording, no CashFrame/Datathon label, ru/kk/en, light/dark, local interactive 3D composition, reduced motion, desktop/mobile. Existing API failure recovery retained.
- [x] Integration: full Python/JS suites, real positive/negative/long MOV, X-13, offline readiness, per-locale PDF, synthetic browser runs, requirements matrix and final evidence/limitations.

## Review focus

1. Failed/cancelled progress must never look complete.
2. A human rule observation cannot silently become model proof or attach to unrelated source/event.
3. Unknown time remains explicit; no fabricated 0-second incident marker.
4. Translation must not change model outputs, source quotations, numeric API fields or evidence hashes.
5. Candidate selection must not read test inputs; no promotion from one training example.

## Execution ledger

- Baseline: main b22ec8c, 117 Python and 10 frontend tests previously passed; metrics file retained.
- Ruling: use existing D:/Datathon on a new branch because the user explicitly targets the running installation; copying private weights into another checkout is unnecessary. No source reset or worktree cleanup.
- Parallel ownership: point localizer, frontend, event table/PDF are independent files. Parent owns API/progress/integration. Shared interfaces fixed before edits.

## Outcome

Implementation and verification completed; no push, merge or public deployment. 154 Python and 23 frontend checks passed. Real API probes, both X-13 profiles, offline processing, three PDF languages, six synthetic viewport/theme/locale checks completed. Binary v2 remains active; point/v3 candidates were not promoted because validation did not establish improvement. Confirmed action types, reliable temporal localization, automatic legal applicability, independent new-angle evaluation and public HTTPS remain documented gaps, not completed capabilities. See `docs/2026-10-18-implementation-audit.md` for the requirements matrix and evidence.
