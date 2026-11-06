# AGENTS.md

Instructions for AI coding agents working on MicroTalk. Read this before
changing code. The repository is a local-first, CPU-only document processing
application; correctness and honest behavior matter more than impressive demos.

## Product invariants

1. **No mocks, fake results, or hardcoded outputs.** Every workflow must call the
   real service path. Progress must be derived from real work, and subtitle
   timings must come from actual generated audio. If a dependency is missing,
   return a clear error or skip a live test—do not fabricate success.
2. **Local inference only.** No external inference APIs. Models are downloaded
   only by explicit `download-models` commands into named volumes. Runtime
   containers operate with Hugging Face offline flags. Never add implicit model
   downloads to request handling or startup.
3. **Only Nginx is published.** The browser talks to the frontend container on
   port 8080 by default. Gateway and microservice ports remain internal.
4. **Files and paths are untrusted.** Use `micro_common.storage.safe_join`,
   `resolve_relative`, `safe_component`, and UUID validation. Never join a
   request value directly into a filesystem path.
5. **Jobs are asynchronous and cancellable.** Redis + RQ owns execution; SQLite
   in `gateway-data` owns public state. Cancellation is cooperative at page,
   batch, sentence, and stage boundaries. Do not mark a job complete before all
   requested artifacts exist.
6. **Edits are not regeneration.** Editing extracted/translated/summary text
   updates text files and metadata only. Existing audio/subtitles remain from
   the previous text and must carry a warning.
7. **Preserve page/paragraph structure.** Translation and extraction outputs use
   `--- Page N ---` markers for PDFs. Do not flatten pages into an opaque blob.

## Repository layout

```
frontend/                    Vue 3 + TypeScript, Vite, custom CSS
  src/api/                   typed API client and auth token state
  src/components/            create workflow, job view/results, player, tools
  tests/                     Vitest tests
services/
  gateway/app/               FastAPI public API, SQLite store, RQ pipeline,
                             clients, artifacts, SSE, auth, rate limiting
  pdf/app/                   PyMuPDF extraction, OCR, previews, retention
  translation/app/           lazy Marian/OPUS-MT engine and language detection
  summary/app/               hierarchical T5 engine and cross-language translation
  tts/app/                   Piper jobs, sentence WAVs, real timeline, MP3/WAV
  media/app/                 FFmpeg transcode/normalize/merge, SRT alignment
shared/python/micro_common/  config, errors, logging, text/SRT, storage, security,
                             ranged file responses
nginx/default.conf           SPA serving and API proxy (SSE-safe)
docker-compose.yml           all services, volumes, healthchecks
tests/                       pytest suite + optional live integration tests
scripts/                     model download, test, smoke check
```

Service packages are all named `app`. Tests load them under synthetic package
names (`microtalk_pdf`, etc.) via `tests/conftest.py` to avoid import collisions.
Do not import multiple real `app` packages in one process.

## Core service contracts

- Gateway: `:8000`, public under `/api`.
- PDF: `:8001`; `/api/v1/pdf/documents`, `/extract`, previews.
- Translation: `:8002`; `/api/v1/translate`, `/detect`, status/languages.
- TTS: `:8003`; `/api/v1/tts/generate`, job status, audio/WAV/timeline.
- Summary: `:8004`; `/api/v1/summary`, status/lengths.
- Media: `:8005`; `/finalize`, `/subtitles`, `/transcode`, `/merge`.
- RQ queue name: `microtalk`; worker entry point: `app.pipeline.run_job`.
- Gateway worker command: `python -m app.worker`.
- Shared model paths: `/models/translation/{pt-en,en-pt}`,
  `/models/summary/{en,pt}`, `/models/piper/{voice_id}`.
- Shared data paths: `/data/gateway`, `/data/pdf`, `/data/tts`, `/data/media`.

Gateway reads TTS and media volumes read-only to serve artifacts. Media reads
the TTS volume read-only and writes only `/data/media`. Gateway and worker share
`gateway-data`; SQLite uses WAL and a 30-second busy timeout. Keep that in mind
for migrations or long transactions.

## Commands

```bash
# Start/rebuild everything
docker compose up -d --build

# Validate Compose
docker compose config --quiet

# Models (explicit, persistent)
./scripts/download-models.sh
docker compose run --rm translation-service download-models
docker compose run --rm summary-service download-models
docker compose run --rm tts-service download-models

# Logs
docker compose logs -f gateway gateway-worker

# Backend tests (lightweight, no Torch/Piper)
./scripts/test.sh
python3 -m venv .venv-test
.venv-test/bin/pip install -r tests/requirements.txt
.venv-test/bin/python -m pytest

# Frontend
cd frontend
npm ci
npm test
npm run build

# Python syntax check
python -m compileall -q shared services

# Live stack checks (requires running Docker stack)
MICROTALK_URL=http://localhost:8080 ./scripts/smoke-test.sh
MICROTALK_URL=http://localhost:8080 pytest tests/integration -v
```

The lightweight backend tests intentionally avoid Torch/Transformers/Piper. If
you add tests that need model weights or FFmpeg, mark them appropriately and do
not run them in the default suite. Live tests must skip when models are absent.

## Coding conventions

- Python 3.11 in images, current host Python for tests. Use type hints, frozen
  dataclass settings, FastAPI/Pydantic v2, and the shared utilities.
- Keep model caches single-model and lazy. Translation and summary must unload a
  model before loading the other direction/language. Do not load both PT and EN
  models concurrently.
- Use `atomic_write_text`/`atomic_write_json` for persisted state. Do not
  partially write JSON or text files.
- Use `ServiceError(code, message, status_code=..., details=...)` for expected
  failures. Do not leak stack traces or filesystem paths to the browser.
- Frontend uses Vue 3 `<script setup lang="ts">`, computed state, and the
  existing custom CSS classes. Do not add a UI framework or external fonts/CDN.
- Keep UI copy honest: say “local”, show missing-model install commands, and
  distinguish estimated subtitle timings from actual-audio timings.
- Respect existing visual language: dark/light theme, responsive layout,
  keyboard focus styles, and accessible labels.
- No API keys, tokens, model weights, PDFs, generated audio, or data directories
  in Git.

## Adding or changing a pipeline stage

Update all of these together:

1. `services/gateway/app/models.py` request/validation models.
2. `build_steps` / `build_initial_state` weights in `pipeline.py`.
3. Pipeline execution and artifact metadata.
4. `_public_job` stripping if a field is large or private.
5. `artifacts.py` text/ZIP/download naming.
6. Frontend `api/types.ts`, create workflow controls, job results rendering.
7. README feature/config/API docs and tests.

Progress weights are relative; a stage must call `_begin`, `_update` with real
progress, and `_complete` or fail. A missing optional artifact should produce a
warning/skip, not a fabricated file.

## Subtitle/audio invariants

- TTS writes one real WAV per sentence and builds `timeline.json` from measured
  frames. Never replace this with fixed per-character/per-word estimates.
- Media receives the actual finalized audio relative path and probes its real
  duration. Segment timings are normalized to that duration; warn when scaling
  or clamping occurs.
- Gateway stores SRT, VTT, and `cues.json` from the same segment list. The player
  uses `cues.json`. SRT/VTT/player timings must not diverge.
- WAV artifact storage can be `media` (final encoded WAV) or `tts` (master WAV)
  depending on format. Resolve each with the correct root in downloads and ZIP.
- TTS audio endpoints support Range. Do not replace `ranged_file_response` with
  a full-file-only response.

## Security checklist

- Validate upload headers, extensions, sizes, page counts, and selected pages.
- Use UUID checks for document, job, media, and TTS IDs.
- Keep `safe_join`/`resolve_relative` containment; reject absolute paths.
- Do not expose internal service ports in Compose.
- Keep auth middleware protecting `/api/*` except the explicitly public paths.
  Media elements/EventSource use the `token` query parameter by design.
- Rate-limit expensive endpoints via Redis; fail open only when Redis is down
  and log it.
- Use non-root UID/GID 10001 in images. New writable paths must be owned by it.
- Model downloads are explicit; runtime must not fetch arbitrary repositories
  from user input.

## Testing expectations

Before handing off a change:

```bash
python -m compileall -q shared services
(cd frontend && npm test && npm run build)
.venv-test/bin/python -m pytest
docker compose config --quiet
```

If Docker is available and disk permits, build affected images and run a real
workflow. Check:

- `/healthz`, `/api/health`, `/api/v1/catalog`.
- Upload/inspect/preview a real PDF, extraction with selected pages.
- Translate PT↔EN and confirm page markers.
- Summarize long text and inspect warnings.
- TTS MP3 and WAV, Range playback, SRT/VTT downloads and cue sync.
- Cancel a running workflow; delete a completed job.
- Auth on/off, including audio and SSE with a token.

The repository may be constrained by disk/network. Do not claim an end-to-end
run succeeded unless it actually did; report skipped checks honestly.

## Environment pitfalls

- The host may have very little free disk. Model/CPU image builds are large.
- `docker compose down -v` destroys models and user data. Never run it to “fix”
  an issue without explicit confirmation.
- Named volumes must be writable by UID 10001. Avoid root-owned bind mounts.
- The frontend has a generated `dist/` and `node_modules/`; do not commit them.
- `frontend/package-lock.json` is authoritative; use `npm ci` in builds.
- Nginx must keep SSE buffering off and long timeouts on both the events
  location and `/api/`.
- The frontend uses hash routing. Preserve `#/jobs/<id>`, `#/library`, `#/tools`,
  `#/create` behavior.
- Summary’s translation calls use the internal translation service URL, not the
  gateway. Do not route internal service calls through the browser-facing proxy.
