# MicroTalk

MicroTalk is a local-first document studio. Upload a PDF or paste text, extract
the pages you need (with optional OCR), translate between Portuguese, English
and French, summarize, narrate with local neural voices, and download synchronized audio,
subtitles, text, and a ZIP bundle. Everything runs on your machine with CPU-only
models; no external inference API is used.

The browser talks to one Nginx container. Nginx serves the Vue application and
reverse-proxies `/api` to the gateway; every other service stays on a private
Docker network.

## Features

- **Real PDF extraction** with PyMuPDF, layout-aware reading order, page
  previews, 1-based page ranges or arbitrary page sets, and Tesseract OCR for
  scanned pages. Scanned documents are detected at upload time.
- **PT / EN / FR translation** with local Hugging Face Marian/OPUS-MT models,
  automatic language detection, page/paragraph-preserving segments, and
  sentence-aware chunking. Portuguese↔French is routed through English because
  the local catalog uses direct PT↔EN and EN↔FR models.
- **Hierarchical summarization** with compact local T5 models in English and
  Portuguese. Long documents are chunked and reduced recursively; a summary can
  be requested in the other language and is translated locally.
- **Piper speech synthesis** with six fixed voices (European Portuguese,
  Brazilian Portuguese, US English, female Dii voices for both Portuguese
  variants, and French Siwis), selectable speed, MP3 or WAV output, and optional
  FFmpeg normalization. The narrator can keep long sentences whole and ignore
  Bible-style verse numbers at the start of phrases.
- **Subtitle timings from actual audio.** Piper writes one WAV per sentence; the
  TTS service concatenates those files and records their real start/end times.
  The media service then aligns the cues to the final encoded audio and produces
  SRT (plus WebVTT and JSON cues at the gateway). No fixed guessed timings.
- **Custom audio player** with HTTP Range requests, seek, volume, playback speed,
  and transcript highlighting synchronized to the generated cue timings.
- **Editable results.** Extracted text, translation, and summary can be edited
  and downloaded. Audio/subtitles are intentionally not silently regenerated
  after an edit; the UI warns that a new workflow is required.
- **Real job state.** Redis + RQ queue, SQLite metadata, Server-Sent Events with a
  polling fallback, per-stage progress from the actual services, cancellation,
  job history, and persistent artifacts.
- **Downloads** for extracted text, translation, summary, MP3, WAV, SRT, VTT and
  a ZIP containing every available result plus `job.json`.
- **Safety defaults**: path containment checks, upload size/type validation,
  non-root containers, optional bearer-token auth, Redis-backed rate limits,
  retention cleanup, and only one published port.

## Architecture

```text
                         Browser
                            │
                     http://localhost:8080
                            │
                 ┌──────────▼──────────┐
                 │  frontend (Nginx)   │  static Vue 3 app + /api reverse proxy
                 │  port 80 internal   │
                 └──────────┬──────────┘
                            │ /api
                 ┌──────────▼──────────┐
                 │       gateway       │  public API, auth, jobs, SSE, downloads
                 │    port 8000        │
                 └───┬───────┬─────────┘
        enqueue job  │       │  HTTP calls + shared volumes
             ┌───────▼──┐    │
             │  Redis   │    ├──────────────► pdf-service         :8001
             │   + RQ   │    ├──────────────► translation-service :8002
             └───────┬──┘    ├──────────────► tts-service         :8003
                     │       ├──────────────► summary-service     :8004
             ┌───────▼────┐  └──────────────► media-service       :8005
             │ gateway-   │
             │ worker     │  runs app.pipeline.run_job from the RQ queue
             └────────────┘
```

All service ports are internal-only. The gateway and worker share the
`gateway-data`, `tts-data` (read-only), and `media-data` (read-only) volumes.
The media service reads the TTS volume and writes finalized audio. Models live
in dedicated persistent volumes.

### Services and ports

| Service | Internal URL | Responsibility |
| --- | --- | --- |
| frontend | `http://frontend:80` | Vue SPA, static files, Nginx proxy |
| gateway | `http://gateway:8000` | Public API, validation, SQLite jobs, SSE, artifacts |
| gateway-worker | — | RQ worker running the extraction/translation/summary/TTS/media pipeline |
| pdf-service | `http://pdf-service:8001` | Upload/inspect/extract/preview, OCR, document retention |
| translation-service | `http://translation-service:8002` | PT/EN/FR local translation; PT↔FR pivots through English |
| tts-service | `http://tts-service:8003` | Piper synthesis, sentence WAVs, timeline, MP3/WAV |
| summary-service | `http://summary-service:8004` | Hierarchical local summarization |
| media-service | `http://media-service:8005` | FFmpeg transcode/normalize/merge, SRT from real timings |
| redis | `redis:6379` | RQ queue and rate-limit counters |

### Persistent volumes

| Volume | Mount | Contents |
| --- | --- | --- |
| `translation-models` | `/models/translation` | PT↔EN and EN↔FR model files |
| `translation-cache` | `/models/.hf-cache` | Download cache for translation |
| `summary-models` | `/models/summary` | EN/PT T5 model files |
| `summary-cache` | `/models/.hf-cache` | Download cache for summaries |
| `piper-models` | `/models/piper` | Piper voice ONNX files and configs |
| `pdf-data` | `/data/documents` | Uploaded PDFs and metadata |
| `pdf-tmp` | `/data/tmp` | Resumable upload/temporary PDF work |
| `tts-data` | `/data/tts` | Per-job sentence WAVs, master WAV, MP3, timeline |
| `media-data` | `/data/media` | Finalized encoded audio and SRT records |
| `gateway-data` | `/data/gateway` | SQLite database and per-job text/ZIP/cue files |
| `redis-data` | `/data` | Redis AOF/RDB queue persistence |

`docker compose down` removes containers but keeps these volumes.
`docker compose down -v` deletes all documents, results, and downloaded models.

## Requirements

- Docker Engine 24+ and Docker Compose v2.20+.
- Linux x86_64 is the primary target (the CPU PyTorch wheels are x86_64).
- 16 GB RAM recommended. The gateway/PDF/frontend are light; translation and
  summarization each load one model at a time by design.
- Free disk: roughly 10–15 GB for images/build layers plus 3–4 GB for models,
  documents, and generated audio. Check with `df -h`.
- Internet access for the first image build and for the explicit model download
  commands. Normal inference is offline.
- Tesseract `por` and `eng` language files are included in the PDF image.

## Quick start

```bash
git clone <your-repository-url> micro-talk
cd micro-talk

cp .env.example .env          # optional; defaults work out of the box
docker compose up -d --build  # build images and start the stack
docker compose ps             # wait for healthy services

# Required once: download local model weights into persistent volumes.
./scripts/download-models.sh

open http://localhost:8080    # or visit http://localhost:8080 in a browser
```

The stack starts successfully before models are installed: the catalog shows
which models/voices are missing, and jobs that need one fail with an explicit
install command. Nothing is downloaded implicitly at runtime.

The first build can take a long time because CPU PyTorch and ONNX Runtime are
installed. Model downloads can also take a while. Useful progress commands:

```bash
docker compose logs -f --tail=100 gateway
docker compose logs -f --tail=100 gateway-worker
docker compose logs -f --tail=100 tts-service translation-service summary-service
```

If you change source code or Dockerfiles:

```bash
docker compose up -d --build
```

Stop the stack while keeping data:

```bash
docker compose down
```

## Installing models

The only supported download path is the explicit Compose command. Weights are
written to named volumes and are not part of the images or Git repository.

```bash
# Everything (translation + summarization + Piper voices)
./scripts/download-models.sh

# Or one service at a time
docker compose run --rm translation-service download-models
docker compose run --rm summary-service download-models
docker compose run --rm tts-service download-models
```

| Target | Models | Approximate download |
| --- | --- | --- |
| translation | PT↔EN (`opus-mt-roa-en`, `opus-mt-tc-big-en-pt`) and EN↔FR (`opus-mt-en-fr`, `opus-mt-fr-en`) | ~1.8 GB |
| summary | `farleyknight/cnn_dailymail-summarization-t5-small-2022-09-08`, `rhaymison/t5-portuguese-small-summarization` | ~500 MB |
| tts | Tugão and Dii (pt-PT), Faber and Dii (pt-BR), Siwis (fr-FR), Lessac (en-US) Piper voices | ~380 MB |

Sizes vary by repository revision. To use different models or revisions, set
the variables in `.env` (see `.env.example`) **before** running the download
command, then restart the affected service.

The images set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` for normal
operation. After images and models are present, translation, summarization, OCR
and speech do not contact the internet. Piper voices come from the
`rhasspy/piper-voices` and `OpenVoiceOS` Hub repositories only during the
explicit download step. The Dii voice models are licensed
CC BY-NC-ND 4.0, and the French Siwis voice uses CC-BY 4.0 data; check those
licenses before commercial use.

## Using MicroTalk

### 1. Choose a source

- **PDF**: drag a PDF or choose a file. MicroTalk validates the file header,
  size, page count, and whether the sampled pages look scanned. Page thumbnails
  help you choose pages. Selection modes are:
  - **All pages**
  - **Range** (inclusive, 1-based)
  - **Custom pages** (click thumbnails, or enter comma-separated pages/ranges)
- **Text**: paste up to `GATEWAY_MAX_TEXT_CHARS` characters (500,000 by default).

**Document language** is always available in the Source step, even when
translation is disabled. It is shared by translation, OCR hints, and narration
voice matching. With `Detect automatically`, the language is resolved when the
workflow starts and the selected voice is verified against it.

OCR is off by default. Turn it on for scanned pages or pages with little
extractable text. OCR uses Tesseract with `por+eng+fra` and runs at
`OCR_DPI` (200 by default) inside the PDF service.

### 2. Choose processing

- **Translate** is on by default with target English. Source language can be
  `auto`, Portuguese, English, or French; target language can be English,
  Portuguese, or French. Pages and paragraphs are preserved; the translation
  result includes `--- Page N ---` markers for PDF input. French↔Portuguese
  uses the two direct English routes as a pivot.
- **Summarize** is optional. Choose short/medium/long and either a fixed output
  language or follow the document language. If source and requested summary
  languages differ, the summary is generated in the source language and then
  translated by the local translation service.
- **Narrate** is on by default. The final text language determines which voice
  is valid. The UI auto-selects a matching installed voice when possible and
  warns about a mismatch. French narration uses the French Siwis voice. If you
  narrate a summary, choose whether the summary language or the translated
  document language applies.
- **Speech source**: narrate the full content or the generated summary.
- **Keep sentences whole** is on by default: long sentences are not split at
  commas for audio/subtitles, so phrases stay intact. A hard safety limit
  (`TTS_PHRASE_MAX_CHARS`, 2000 by default) still splits extreme sentences.
- **Ignore leading numbers** removes Bible-style verse labels such as
  `1 No princípio…`, `2. E a terra…`, or `1:1` before narration and subtitles.
  It is off by default.
- **Speed** is 0.5–2.0. **Format** is MP3 or WAV. **Normalize** applies FFmpeg
  `dynaudnorm` when the media service finalizes the audio. Pitch is not offered
  because the bundled Piper voices do not support pitch shifting.

### 3. Process and monitor

Submitting creates a real RQ job. The job view shows the actual stage progress
reported by each service, warnings, and elapsed time. Server-Sent Events push
state changes; if SSE is unavailable the UI falls back to polling. Cancel is
cooperative: the worker checks between pages/batches/sentences, cancels the TTS
job, and stops at the next safe point.

The default queue concurrency is one worker. Scale workers if the machine has
enough CPU and RAM:

```bash
docker compose up -d --scale gateway-worker=2
```

Each worker can run one workflow at a time; ML services still serialize model
loading internally.

### 4. Results

- **Extracted text**, **translation**, and **summary** tabs are editable. Saving
  writes the text file and updates the artifact metadata. Existing audio and
  subtitles were generated from the previous text; MicroTalk adds a warning and
  does not pretend they were regenerated.
- **Audio player** streams through the gateway with byte ranges. It supports
  seeking, volume, mute, and playback-rate changes. Subtitles highlight the cue
  covering the current audio time.
- **Downloads** include TXT, MP3, WAV (when available), SRT, VTT, and a ZIP.
  The ZIP contains every available artifact plus a `job.json` manifest. Missing
  optional results are skipped rather than represented by placeholders.

### Quick tools

The **Quick tools** view runs standalone translation, summarization, or TTS
without creating a document workflow. Direct TTS shows the same real sentence
timeline and can be cancelled while running. These endpoints are still local;
the browser never sends text to a third-party API.

## Subtitle timing

Subtitle accuracy is a core design constraint:

1. The TTS service splits the text into sentences and keeps each sentence whole
   by default (up to `TTS_PHRASE_MAX_CHARS`), synthesizes each sentence to its
   own WAV file, and measures each actual WAV file.
2. It concatenates the sentence WAVs with a configurable gap
   (`TTS_SENTENCE_GAP_MS`, 120 ms by default) and records exact start/end times
   for every sentence. This timeline is persisted as `timeline.json`.
3. The gateway passes the sentence timings and the actual finalized audio path
   to the media service.
4. The media service probes the final encoded audio duration, normalizes the
   cue intervals, scales or clamps them if the encoded duration differs, and
   writes SRT. The gateway writes WebVTT and `cues.json` from the same segments.
5. The web player uses `cues.json`, so highlighting follows the same timings as
   the downloadable subtitles.

There is no word-level forced alignment. Cues are sentence-level and tied to
the actual generated audio, not a fixed words-per-minute estimate. The
text-only media-subtitle endpoint can estimate timings only when no audio is
provided; the normal workflow always supplies real TTS timings.

## Configuration

Copy `.env.example` to `.env`. Compose gives every variable a default, so an
empty `.env` is valid. The most useful settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MICROTALK_PORT` | `8080` | Host port for the only published service |
| `GATEWAY_AUTH_TOKEN` | empty | Optional bearer token; empty disables auth |
| `MAX_UPLOAD_MB` | `100` | PDF upload limit (gateway and PDF service) |
| `MAX_PDF_PAGES` | `2000` | Maximum pages in an uploaded PDF |
| `GATEWAY_MAX_TEXT_CHARS` | `500000` | Pasted text limit |
| `GATEWAY_MAX_ACTIVE_JOBS` | `12` | Maximum queued/running workflows |
| `GATEWAY_RETENTION_HOURS` | `168` | Gateway job/artifact retention |
| `PDF_RETENTION_HOURS` | `168` | Uploaded PDF retention |
| `TTS_RETENTION_HOURS` | `72` | TTS job audio retention |
| `MEDIA_RETENTION_HOURS` | `168` | Final media retention |
| `CLEANUP_ENABLED` | `true` | Enable periodic retention cleanup |
| `CLEANUP_INTERVAL_SECONDS` | `1800` | Gateway cleanup interval |
| `CPU_THREADS` | `4` | Torch/ONNX thread count |
| `MODEL_IDLE_UNLOAD_SECONDS` | `900` | Unload idle translation/summary models; `0` keeps them |
| `MINIMUM_FREE_DISK_MB` | `500` | Refuse expensive work below this free space |
| `OCR_ENABLED_DEFAULT` | `false` | Default OCR toggle for new PDFs |
| `OCR_LANGUAGES` | `por+eng+fra` | Tesseract languages |
| `OCR_DPI` | `200` | OCR render DPI |
| `DEFAULT_TTS_VOICE` | `pt-PT-tugao` | Initial voice |
| `DEFAULT_TTS_SPEED` | `1.0` | Initial speed |
| `TTS_MAX_SEGMENT_CHARS` | `400` | Segment size when phrase preservation is off |
| `TTS_PHRASE_MAX_CHARS` | `2000` | Hard safety cap for keeping a sentence whole |
| `TTS_MAX_QUEUE` | `20` | Active TTS jobs before HTTP 429 |
| `MEDIA_MP3_BITRATE` | `128k` | Final MP3 bitrate |
| `SUBTITLE_WORDS_PER_MINUTE` | `150` | Only for text-only estimated subtitle mode |
| `RATE_LIMIT_ENABLED` | `true` | Redis rate limiting |
| `LOG_LEVEL` | `INFO` | Service log verbosity |

Model repositories, revisions, prompt prefixes, summary token budgets, TTS text
limits, and FFmpeg timeouts are also documented in `.env.example`.

### Optional access protection

Set a strong value in `.env`:

```dotenv
GATEWAY_AUTH_TOKEN=replace-with-a-long-random-token
```

Restart with `docker compose up -d`. The browser prompts for the token and
sends `Authorization: Bearer …` for API calls. Media elements and EventSource
cannot set headers, so those URLs carry the token as a `token` query parameter.
The token is stored only in the browser’s `localStorage`.

This is application-level protection, not transport encryption. For access over
an untrusted network, put MicroTalk behind a TLS reverse proxy or VPN. Do not
publish port 8000 or the internal service ports.

### Retention and cleanup

Each service periodically removes terminal jobs/files older than its retention
setting. The gateway cleanup also asks TTS and media services to delete their
records for expired workflows. The default retention is one week for documents,
jobs, and media, and 72 hours for TTS intermediates. Active jobs are not
deleted. Adjust the retention variables if you need results longer; set
`CLEANUP_ENABLED=false` only if you have planned disk capacity.

## API overview

The public surface is the gateway, proxied under `/api`. Internal service ports
are not published.

| Method and path | Purpose |
| --- | --- |
| `GET /health`, `GET /api/health` | Gateway and Redis health |
| `GET /api/v1/auth/status` | Whether a token is required |
| `GET /api/v1/catalog` | Translation, summary, TTS, and service health |
| `POST /api/v1/documents` | Upload a PDF (multipart `file`) |
| `GET /api/v1/documents/{id}` | PDF metadata |
| `GET /api/v1/documents/{id}/pages/{n}/preview.png` | Page thumbnail |
| `DELETE /api/v1/documents/{id}` | Delete a PDF |
| `POST /api/v1/jobs` | Create a workflow |
| `GET /api/v1/jobs` | Paginated job library |
| `GET /api/v1/jobs/{id}` | Current public job state |
| `GET /api/v1/jobs/{id}/events` | SSE job updates |
| `POST /api/v1/jobs/{id}/cancel` | Cooperative cancel |
| `DELETE /api/v1/jobs/{id}` | Delete a terminal job |
| `GET/PATCH /api/v1/jobs/{id}/text/{kind}` | Read/edit extracted, translation, summary |
| `GET /api/v1/jobs/{id}/cues` | Sentence cue timings |
| `GET /api/v1/jobs/{id}/downloads/{kind}` | TXT, MP3, WAV, SRT, VTT, ZIP |
| `POST /api/v1/direct/translate` | Standalone translation |
| `POST /api/v1/direct/summarize` | Standalone summary |
| `POST /api/v1/direct/tts` | Start standalone TTS |
| `GET/DELETE /api/v1/direct/tts/{id}` | Poll/cancel standalone TTS |

Interactive API documentation is available at `/docs` on the gateway when
running locally; Nginx does not proxy `/docs`. Use the internal gateway only
during development, or add a proxy location if you need it.

## Development

### Frontend

```bash
cd frontend
npm ci
npm run dev        # Vite on :5173, proxies /api to :8000 by default
npm test           # Vitest + jsdom
npm run build      # vue-tsc typecheck + production build
```

For local development, start the backend services with Compose and expose the
gateway temporarily, or set `VITE_API_PROXY`:

```bash
VITE_API_PROXY=http://localhost:8000 npm run dev
```

### Backend

The Python services are FastAPI applications under `services/*/app`. Shared
utilities live in `shared/python/micro_common`. Run the lightweight backend
suite without Torch/Piper:

```bash
./scripts/test.sh
```

Or directly:

```bash
python3 -m venv .venv-test
.venv-test/bin/pip install -r tests/requirements.txt
.venv-test/bin/python -m pytest
```

The backend suite covers shared text/SRT/Range/path helpers, real PDF
inspection/extraction/preview with generated PDFs, gateway SQLite lifecycle and
ZIP artifacts, translation batching, media subtitle timing, TTS segmentation,
and language resolution. It intentionally does not require model weights.

### Live Docker integration

```bash
MICROTALK_URL=http://localhost:8080 \
GATEWAY_AUTH_TOKEN=... \
  pytest tests/integration -v
```

The live tests check the proxy, gateway, Redis, catalog, and—when translation
models are installed—create a real text workflow, poll it to completion, fetch
translated text and the ZIP, then delete it. No results are mocked.

### Validation commands

```bash
docker compose config --quiet
python -m compileall -q shared services
(cd frontend && npm test && npm run build)
.venv-test/bin/python -m pytest
./scripts/smoke-test.sh
```

## Project layout

```text
.
├── docker-compose.yml
├── .env.example
├── nginx/default.conf              # SPA + /api proxy, SSE-safe buffering
├── frontend/
│   ├── Dockerfile                  # Node build → Nginx runtime
│   ├── src/                        # Vue 3 + TypeScript application
│   └── tests/                      # Vitest utilities and component tests
├── services/
│   ├── gateway/app/                # public API, RQ pipeline, SQLite, artifacts
│   ├── pdf/app/                    # upload, extraction, OCR, previews
│   ├── translation/app/            # local Marian/OPUS-MT engine
│   ├── summary/app/                # hierarchical T5 summarization
│   ├── tts/app/                    # Piper jobs, sentence timeline
│   └── media/app/                  # FFmpeg and SRT alignment
├── shared/python/micro_common/     # config, text, storage, security, HTTP files
├── tests/                          # pytest + optional live integration tests
└── scripts/                        # model download, test, smoke check
```

## Troubleshooting

**The catalog says models are missing.** Run
`./scripts/download-models.sh` (or the per-service command), then refresh. The
UI and API intentionally do not download weights during requests.

**Model download fails with permission errors.** The named volumes must be
writable by UID/GID 10001. Recreate empty model volumes with
`docker compose down -v` (this deletes all data) and rerun the download. Avoid
bind-mounting a root-owned host directory over `/models`.

**`queue_unavailable` / gateway health says Redis is down.** Run
`docker compose ps` and `docker compose logs redis`. The gateway and worker both
need Redis. Restart Redis; jobs already in the SQLite database that never
reached the queue can be recreated.

**A workflow fails with `tts_voice_language_mismatch`.** The chosen voice speaks
a different language than the final text. Translate to English and select
`en-US-lessac`, or keep Portuguese and choose a `pt-PT`/`pt-BR` voice. If the
source is `auto`, the error reports the detected language.

**OCR is unavailable or poor.** The image installs `por`, `eng`, and `fra`
Tesseract data. Use a higher `OCR_DPI` for small scans, select fewer pages, and inspect
the previews. OCR on large ranges is CPU-heavy.

**SSE is buffered or disconnects through another proxy.** Nginx sets
`proxy_buffering off`, `X-Accel-Buffering: no`, and long read timeouts for the
events endpoint. Replicate those settings in any extra proxy. The frontend
falls back to polling if SSE cannot connect.

**Audio does not play.** Verify `docker compose logs media-service` and that
FFmpeg is available (`/health` on the internal media service). MP3 is broadly
compatible; WAV is also offered. Byte-range requests require the `/api` proxy
to pass through—MicroTalk’s Nginx config does.

**Results disappeared.** Retention cleanup is enabled by default. Check
`GATEWAY_RETENTION_HOURS`, `PDF_RETENTION_HOURS`, `TTS_RETENTION_HOURS`, and
`MEDIA_RETENTION_HOURS`.

**Docker build runs out of disk.** The CPU PyTorch images are large. Remove
unused images/build cache (`docker system prune`) and ensure 10–15 GB free
before building. The gateway, PDF, media, and frontend images are much smaller
and can be built separately while debugging.

**A job seems stuck.** Check the worker logs and `docker compose ps`. The gateway
marks jobs stale after `JOB_TIMEOUT_SECONDS + 300` without progress. Redis
restart marks unfinished TTS jobs failed on TTS service startup rather than
pretending they completed.

## Limitations and honest caveats

- Translation supports Portuguese, English, and French. Summarization still
  supports only Portuguese and English, so French final text cannot be
  summarized; translate it to EN/PT first or disable summarization. OCR
  languages are limited to what the image installs.
- Local CPU inference is slower than hosted GPUs. Long PDFs, OCR, and TTS take
  real time; progress reflects actual work, not a timer.
- Subtitles are sentence-level. Timings come from actual sentence WAVs and are
  normalized to the final audio, but there is no word-level forced alignment.
- Summaries use compact T5 models. They can omit details or produce imperfect
  phrasing; the UI warns to verify names, dates, and figures.
- Machine translation and OCR can contain errors. Review critical documents.
- Editing a text result does not regenerate existing audio/subtitles. Start a
  new workflow for a fully consistent bundle.
- Pitch adjustment is unavailable for the bundled Piper voices. MicroTalk
  reports this rather than silently ignoring the setting.
- Default queue concurrency is one worker. Increasing it also increases peak
  memory/CPU pressure.

## Privacy

Uploads, extracted text, translations, summaries, audio, and job metadata stay
in Docker volumes on the host. The application has no telemetry or third-party
inference calls. Network access is needed to pull images and, once, to download
models from Hugging Face. After that the stack can run offline.
