# Local Video Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, reproducible video review service with real training, honest evaluation, the required jury API and an evidence-focused Russian interface.

**Architecture:** FFmpeg decodes video locally; a frozen compact visual encoder feeds a trained temporal-window classifier. A single FastAPI service exposes the required API, a bounded background worker, persistent job state, annotation tools and the review interface. Confirmed event categories and normative links remain separate from binary weak supervision.

**Tech Stack:** Python 3.11, existing CPU PyTorch 2.6.0 / torchvision 0.21.0, NumPy, scikit-learn, Pillow, FFmpeg, FastAPI, SQLite, browser-native JavaScript and CSS; exact installed package versions pinned after environment inspection.

**Spec:** `docs/superpowers/specs/2026-10-17-local-video-review-design.md`

## Global Constraints

- Work in `D:\Datathon`; source video directory `D:\open\open\videos` and workbook `D:\open\open\open_labels.xlsx` remain unchanged.
- No video, frames or descriptions of video content go to cloud AI services. No face identification.
- Technical repository name `cashframe-lab`; no invented product name, slogan or logo.
- 42 files represent 40 SHA-256 groups. `Х-4` / `Х-47` have points at 57 and 68 seconds; preserve both without assuming two events. `Х-5` / `Х-48` are the other exact duplicate pair.
- Split by source groups before training; fix seed and validation-only threshold selection. Never train on jury inputs.
- Unknown portions of positive video are not automatically labeled negative or positive. Type labels and interval labels require confirmation.
- Baseline encoder stays unchanged. A distance-to-normal baseline is identified as such; it does not fulfill an unmeasured semantic zero-shot claim.
- Required routes: POST `/api/v1/analyze`, multipart `file`, and GET `/api/v1/jobs/{job_id}`, authenticated by `X-API-Key`.
- Required statuses: `processing`, `done`, `failed`. Required done payload: `job_id`, `status`, `verdict`, `violations`; verdict is `no_violation` or `violation`.
- Violation fields: `type`, `timecode_sec`, `description`, `evidence_frames`; 1–3 original JPEGs with `timestamp_sec` and `image_base64`.
- Processing deadline is 300 seconds including queue/preparation/response; technical failures never become a negative verdict.
- Keep videos, workbook, annotations, weights, embeddings, runtime state and secrets outside Git. No runtime model downloads or external frontend dependencies.
- Publish only measured claims; a binary prototype without verified categories and rules is not declared fully compliant.

## Review Focus

- Duplicate recordings with different point labels: one split group, all point labels preserved, no silent overwrite.
- Variable frame rate and nonzero stream start: evidence timestamps use decoded presentation times relative to original video start.
- Valid MOV container with truncated data: complete decode fails explicitly; valid metadata alone is insufficient.
- Restart or timeout during inference: persisted processing jobs become failed; temporary resources and queue capacity are released.
- Unconfirmed categories or unverified legal sources: no invented specific type or legal conclusion appears in API or UI.

## File Structure and Interfaces

`src/cashframe/` contains small modules: `config.py`, `schemas.py`, `data.py`, `video.py`, `features.py`, `learning.py`, `events.py`, `rules.py`, `storage.py`, `worker.py`, `api.py`, `cli.py`. `web/` contains `index.html`, `app.js`, `styles.css`, `i18n.ru.js`. `config/` contains public rule and priority registries without private annotations. `tests/` contains synthetic fixtures and meaningful unit/integration checks. `docs/` contains usage, architecture and a sanitized measured report. All private products go under ignored `local/`.

Core interfaces:

- `audit_dataset(video_dir: Path, labels_path: Path, output_dir: Path) -> dict`
- `build_split(manifest: list[dict], seed: int) -> dict[str, list[str]]`
- `probe_video(path: Path) -> VideoInfo`
- `iter_samples(path: Path, sample_fps: float, size: int) -> Iterator[tuple[float, np.ndarray]]`
- `extract_features(path: Path, encoder: Encoder, sample_fps: float) -> FeatureSequence`
- `train_experiment(manifest_path: Path, split_path: Path, feature_dir: Path, output_dir: Path) -> dict`
- `analyze_video(path: Path, model_dir: Path, work_dir: Path, stage_callback: Callable[[str], None]) -> AnalysisResult`
- `JobStore.create() -> str`; `JobStore.get(job_id: str) -> dict`; `JobStore.update(job_id: str, **fields) -> None`
- `create_app(settings: Settings) -> FastAPI`

### Task 1: Reproducible dataset audit and leakage-safe split

**Files:** `pyproject.toml`, `src/cashframe/{__init__,config,data,cli}.py`, `tests/test_data.py`, `README.md`.

**Produces:** audit manifest and a fixed group split in ignored `local/dataset/`; CLI audit/split commands; configuration from environment.

- [ ] Write tests `test_russian_minute_timecode` asserting `1минута 8 секунда == 68`, `test_duplicate_group_keeps_both_points` asserting points `[57, 68]`, `test_split_groups_do_not_overlap` asserting pairwise disjoint group IDs and both classes in each partition, and `test_unknown_video_has_no_imputed_label`.
- [ ] Run `python -m unittest discover -s tests -p test_data.py -v`; confirm expected import failures before implementation.
- [ ] Implement OOXML workbook reading, Unicode-aware ID matching, SHA-256 grouping, FFprobe metadata and full FFmpeg decode checks. Exact file hashes, original names and raw point labels remain in the private manifest. Fix seed 42; assign approximate 60/20/20 stratified groups with no fragments split across partitions.
- [ ] Run the tests, audit the actual dataset and save the fixed split. Measure sequential low-resolution visual similarity only for related-scene investigation; do not invent a scene-disjoint guarantee from common backgrounds.
- [ ] Pin runtime versions after inspection, document commands and commit code plus sanitized aggregate audit; exclude actual per-video data.

### Task 2: Local decoding, encoder and cached features

**Files:** `src/cashframe/{video,features}.py`, `tests/test_video.py`, `docs/model-licenses.md`.

**Consumes:** manifest and split from Task 1. **Produces:** timestamped feature sequences and encoder metadata, entirely local.

- [ ] Write synthetic MOV tests for nonzero start time, VFR sample timestamp ordering, full-decode failure on truncation and JPEG byte decoding with Pillow. Assert evidence times stay inside original duration and are derived from frame presentation timestamps.
- [ ] Run `python -m unittest discover -s tests -p test_video.py -v`; confirm failures.
- [ ] Verify official licenses for torchvision and selected pretrained weights, recording source URLs and unresolved dataset caveats. Benchmark a single compact CPU encoder with one FFmpeg thread and a small batch; measure RSS and fail with a clear resource message on inability to run. Do not automatically close user applications or download large models.
- [ ] Implement bounded streaming decode and frozen encoder extraction using installed weights only, with configurable sampling and cache keys containing file SHA, encoder hash and sampling config. Capture original PTS; sequential decode checks cover the complete stream.
- [ ] Run tests and extract features for unique source groups. Save resource/timing data; compare sampling choices on known annotated points and flag unresolved short-action loss. Commit source and license record.

### Task 3: Real binary training, baseline and held-out report

**Files:** `src/cashframe/{learning,events}.py`, `tests/test_learning.py`, `docs/experiment-report.md`.

**Consumes:** fixed split and frozen features. **Produces:** baseline and trained model directories, configuration, logs, validation threshold and held-out metrics.

- [ ] Write tests asserting validation-only threshold selection, unknown positive-video regions excluded from supervised negative windows, overlapping windows cover complete duration, repeat detections merge and separated peaks remain separate.
- [ ] Run `python -m unittest discover -s tests -p test_learning.py -v`; confirm failures.
- [ ] Implement normal-reference distance baseline with frozen encoder and explicitly name its nonsemantic limits. Train a regularized classifier over temporal window summaries near positive point labels and negative-video windows. Save all training/validation/test group IDs, seed, window config, objective/optimization history and weights. Preserve baseline and trained variants.
- [ ] Select model and threshold on validation; evaluate test once with video precision/recall/F1, confusion matrix, per-video errors and point-localization error. If semantic zero-shot, category accuracy or evidence relevance remain unmeasured, record them as unavailable. Never infer category accuracy from binary labels.
- [ ] Benchmark full processing of the longest video including decode, refinement, JPEGs and response preparation, recording wall time and RSS. Add queue-time verification in Task 6. Save aggregate sanitized report before proceeding to main frontend. Commit source and honest report.

### Task 4: Human annotation and verified event taxonomy

**Files:** `src/cashframe/annotations.py`, `web/annotate.html`, `web/annotate.js`, `tests/test_annotations.py`, `docs/annotation.md`.

**Consumes:** original videos and point labels via authenticated local endpoints. **Produces:** private confirmed annotations separate from automatic suggestions.

- [ ] Write tests that automatic proposals cannot become confirmed without an explicit confirmation record, end precedes start is rejected, and a category needs an observable description plus rule linkage or explicit unresolved status.
- [ ] Run `python -m unittest discover -s tests -p test_annotations.py -v`; confirm failures.
- [ ] Implement local video/timestamp annotation with source group, interval, visible fact, category, visibility, reviewer and confirmation date. Keep proposals separate, preserve originals and do not transmit content to external AI.
- [ ] Add a small annotation screen with frame stepping and local save. If reliable types cannot be derived locally, ask the user to confirm annotations through this working screen; continue independent binary/API work without claiming full taxonomy readiness.
- [ ] Train/evaluate category classification only when confirmed labels support a valid grouped split. Otherwise explicitly keep category support unavailable. Commit code and instructions, not annotations.

### Task 5: Verified rules and transparent priorities

**Files:** `src/cashframe/rules.py`, `config/{rules,priorities}.json`, `tests/test_rules.py`, `docs/rules.md`.

**Consumes:** confirmed event categories; public primary RK normative sources. **Produces:** source-grounded local registry and transparent priority explanations.

- [ ] Write tests rejecting generated/unknown rule IDs, unverified rules as conclusions, missing applicability conditions and priority based solely on classifier score.
- [ ] Run `python -m unittest discover -s tests -p test_rules.py -v`; confirm failures.
- [ ] Research official applicable acts on Adilet/NBRK with edition and verification date. Populate ID/document/section/URL/excerpt/applicability/observable cues/limits only where actually verified. Keep unverified references explicitly disabled; never fabricate a normative match for an unconfirmed event type.
- [ ] Implement deterministic lookup and editable high/medium/low review-priority policy based on confirmed event properties, with unspecified priority as a supported state. Separate visible fact, possible inconsistency and unavailable information.
- [ ] Run tests and source checks. Commit registry and documentation without private event descriptions.

### Task 6: Exact API, durable jobs and offline pipeline

**Files:** `src/cashframe/{schemas,storage,worker,api}.py`, `tests/test_api.py`, `.env.example`, `scripts/{start.ps1,smoke_client.py}`.

**Consumes:** trained artifact selected in Task 3, optional confirmed taxonomy/rules. **Produces:** authenticated asynchronous API and original JPEG evidence.

- [ ] Write integration tests for missing/wrong key, multipart `file`, valid MOV, unsupported extension, invalid container, size limit, busy worker, missing artifact, unknown job, analyzer exception, timeout and restart recovery. Assert no technical failure returns `no_violation` and no unconfirmed category becomes a specific claimed violation type.
- [ ] Run `python -m unittest discover -s tests -p test_api.py -v`; confirm failures.
- [ ] Implement exact required response models, constant-time key comparison, environment-only server secret, generated internal filenames, bounded single-worker capacity, SQLite job persistence, stage callbacks, deadline enforcement, temporary cleanup and retention. Extra review/progress/media fields use separate endpoints. Media endpoints also require authorization.
- [ ] Integrate trained inference, merge/refine events, extract 1–3 untouched JPEGs with source timestamps, and prepare an authenticated local browser MP4 with preserved time origin where needed. Persist failed state on timeout/restart; terminate subprocesses and release worker slots.
- [ ] Run API tests and a separate-client real-MOV smoke test validating exact keys, final status, timestamps and JPEG decode. Test worst-case queue/deadline behavior. Disable external sockets in a separate inference smoke process to prove processing uses local assets; do not change machine-wide firewall policy. Commit code and sanitized results.

### Task 7: Evidence-focused Russian interface

**Files:** `web/{index.html,app.js,styles.css,i18n.ru.js}`, `tests/test_web_contract.py`, `docs/interface-checks.md`.

**Consumes:** exact API and additional authenticated review endpoints. **Produces:** upload, processing and result screens from real jobs.

- [ ] Recheck live Forte reference visually before implementation; apply the impeccable skill for a calm banking workspace. Use system/local fonts; no external runtime assets, gradients, fabricated charts, brand name or invented logo.
- [ ] Build upload with session key input, real stage updates, clear error/retry states, large video, synchronized event list/timeline, selected interval, before/event/after evidence and normative details. Show unknown priority and incomplete visibility honestly; sort by priority with chronology toggle.
- [ ] Keep the entered key in memory, not source or persistent browser storage. Fetch protected media as authorized blobs, revoke object URLs and stop polling when leaving a job.
- [ ] Verify keyboard upload, event selection, video seeking, responsive panel order, text badges, contrast and visible focus. Run a real browser at laptop and narrow widths; fix observed errors. No drawn tracking overlays without actual detections.
- [ ] Commit UI and verification notes after actual-model report exists.

### Task 8: Packaging, repository publication and acceptance status

**Files:** `README.md`, `requirements.lock`, `docs/{deployment,limitations,acceptance}.md`, `scripts/{setup.ps1,cleanup.py}`, `.github/workflows/checks.yml`.

**Produces:** private GitHub repository `Mevinss/cashframe-lab`, reproducible local start and measured acceptance report.

- [ ] Create the private repository using the existing authorized GitHub identity after implementation-plan approval. Push source incrementally only after scanning tracked paths and content for private assets and secrets; never push `local/`.
- [ ] Document pinned installation, permitted pretrained-weight setup, preparation/training/evaluation commands, API key setup, readiness, offline startup, storage layout and cleanup. Existing source/model/annotation access remains local.
- [ ] Run complete meaningful unit/integration checks once, actual-video smoke, longest-video benchmark, offline smoke and browser checks. Review code against failure modes; rerun broader tests only after relevant changes.
- [ ] Verify remote default branch, expected files and commit. Record exact tested commands and sanitized results. Do not claim external deployment until an external host/URL is actually configured and reachable from another client.
- [ ] Deliver repository link, startup instructions, audit, real training comparison and remaining blockers. Clearly list unavailable type/rule validation, evidence review, novel-angle testing or public-host access instead of claiming full completion.

## Execution Order and Handoff

Tasks execute sequentially in this chat. Dataset/encoder/training come first; Task 4 provides the human path for missing labels; verified rules and exact API follow; Task 7 is gated by the measured model report. The repository is private, and private local artifacts remain untracked throughout. Use native execution here; no new chats or worker agents are required for this plan.

Plan review is required before product implementation by the active writing-plans skill. If the user explicitly authorizes proceeding without subsequent planning checkpoints, follow that instruction and continue implementation within the approved scope.
