# Video audit v2 implementation plan

> For agentic workers: use superpowers:executing-plans and test-driven-development. Independent model research and PDF implementation may run concurrently under dispatching-parallel-agents.

Goal: execute the user's submitted detailed specification, improving evaluation, evidence reporting and the human annotation workflow without changing the jury API.

Architecture: preserve FastAPI, local subprocess inference and the fixed source split. Run model experiments from cached features using training/validation only and audit the already-viewed test once after selecting a candidate. Reports consume stored job results; a canonical JSON evidence passport binds source, models/configuration and actual JPEG bytes. A separate local PDF renderer consumes that manifest and stored results, without network access. Confirmed human observations remain separate from original model output.

Specification: user-submitted attachment aef54f6b-b988-4cab-a54e-1e89a2a820ac, supplied 2026-10-17. It defines PDF title, content, offline processing, validation protocol and honest completion criteria. Existing design and API contract remain in force.

Global constraints: no cloud AI for private videos/frames/descriptions; no identification of faces; no private source data, annotations, credentials or weights in Git; no new-angle or 85–90% claim without independent evidence; retain X-API-Key, exact jury JSON and 300-second overall limit. Use only the existing local verified rules registry during PDF generation. Reports say preliminary, require authorized review, and never imply bank approval.

## Deliverables

- [x] Audit and experiment: tests for validation-only selection and leakage, reproducible cached-feature experiments, explicit constant baselines, per-video metrics and dataset/angle audit. Owned files: new research module/script/tests and aggregate experiment documentation; selected artifact stays local.
- [x] Report module: ReportLab with bundled redistributable Unicode fonts, canonical passport and artifact hashes, PDF for positive/negative results with before/event/after JPEGs, reviewer space and limitations. Tests verify hashes, escaping, missing data, multilingual extraction and no secret leakage. Owned files: reports module, fonts/licenses, report tests and synthetic example script. API integration handled separately.
- [x] API and annotation workflow: authenticated completed-job PDF/passport routes, cached snapshots, retention behavior, visibility and rule applicability confirmation, no unsupported automatic normative claims. Frontend download actions, visibility field and explicit rule state. Tests cover authorization, unfinished/failed/expired jobs, integrity and annotation provenance.
- [x] Integrate selected model only if validation improvement is material and compatible with time/memory. Save old artifacts and report candidly if no improvement. Do not train on unconfirmed types or transformed test footage.
- [ ] Full regression checks, real offline timing on multiple videos including longest, synthetic browser/PDF visual review, private-asset scan, requirements matrix and team instructions. Commit and push verified code/documentation without overwriting concurrent remote work.

## Review focus

Report generation must never fetch URLs from submitted content. Missing model versions or frame integrity must be explicit, not silently invented. Unknown action types cannot become confirmed by saving a draft. Human rule proposals must not alter original model predictions. PDF generation/download must respect expiry and authentication. Model comparison must not consult test labels during selection; transformed viewpoints do not prove generalization to another camera.

## Inputs still required

Confirmed visual event categories with representative human labels and an independent real different-camera sample are not present. The user has been asked for these while implementation continues. Their absence prevents honest completion of typed recognition and new-angle accuracy acceptance.

Execution ruling: the user submitted the previously prepared detailed design as the implementation instruction; no repeated architecture approval was requested. Modelv2 remains a visibly experimental selectable profile; no real type artifact or new-camera quality is claimed without labels. Final tests85/85 and three API timings are in docs/acceptance-v2.json.
