<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, handleApiError } from "../api/client";
import type { JobState } from "../api/types";
import { copyToClipboard } from "../utils/clipboard";
import { formatDate, statusLabel } from "../utils/format";
import {
  isActive,
  isTerminal,
  outputLabels,
  sourceLabel,
  statusTone,
} from "../utils/jobs";
import Icon from "./Icon.vue";
import JobProgress from "./JobProgress.vue";
import JobResults from "./JobResults.vue";

const props = defineProps<{
  jobId: string;
}>();

const emit = defineEmits<{
  "open-library": [];
  "new-workflow": [];
  deleted: [];
}>();

const job = ref<JobState | null>(null);
const loading = ref(true);
const error = ref("");
const actionError = ref("");
const cancelling = ref(false);
const deleting = ref(false);
const copying = ref(false);
const copyNotice = ref("");
const streamState = ref<"connecting" | "live" | "polling">("connecting");

let source: EventSource | null = null;
let pollTimer: number | undefined;
let sseRetryTimer: number | undefined;
let pollBusy = false;
let disposed = false;
let noticeTimer: number | undefined;

const tone = computed(() => (job.value ? statusTone(job.value.status) : ""));
const hasResults = computed(() => {
  const artifacts = job.value?.artifacts;
  if (!artifacts) return false;
  return Object.values(artifacts).some(
    (artifact) => artifact && typeof artifact === "object" && (artifact as { available?: boolean }).available,
  );
});
const outputBadges = computed(() => outputLabels(job.value?.artifacts));
const errorDetails = computed(() => {
  const details = job.value?.error?.details || {};
  return Object.entries(details)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => [
      key.replaceAll("_", " "),
      typeof value === "object" ? JSON.stringify(value) : String(value),
    ]);
});
const streamLabel = computed(() => {
  if (!job.value || isTerminal(job.value)) return "Finished";
  if (streamState.value === "live") return "Live updates";
  if (streamState.value === "polling") return "Polling updates";
  return "Connecting…";
});

watch(
  () => props.jobId,
  () => void loadJob(true),
  { immediate: true },
);

watch(
  () => job.value?.title,
  (title) => {
    if (title) document.title = `${title} · MicroTalk`;
  },
);

onBeforeUnmount(() => {
  disposed = true;
  stopStreaming();
  if (noticeTimer) window.clearTimeout(noticeTimer);
});

async function loadJob(initial = false) {
  stopStreaming();
  if (initial) {
    loading.value = true;
    job.value = null;
  }
  error.value = "";
  actionError.value = "";
  try {
    const state = await api.getJob(props.jobId);
    applyState(state);
  } catch (err) {
    error.value = handleApiError(err);
  } finally {
    loading.value = false;
  }
}

function applyState(state: JobState) {
  job.value = state;
  if (isTerminal(state)) {
    stopStreaming();
    streamState.value = "live";
    return;
  }
  ensureStreaming();
}

function ensureStreaming() {
  startStream();
  startPolling();
}

function startStream() {
  if (source || disposed || !job.value || isTerminal(job.value)) return;
  try {
    const eventSource = new EventSource(api.eventUrl(props.jobId));
    source = eventSource;
    streamState.value = "connecting";
    eventSource.addEventListener("open", () => {
      streamState.value = "live";
    });
    eventSource.addEventListener("job", (event) => {
      try {
        const state = JSON.parse((event as MessageEvent).data) as JobState;
        streamState.value = "live";
        applyState(state);
      } catch {
        // A malformed event is ignored; polling remains a safe fallback.
      }
    });
    eventSource.addEventListener("done", () => {
      closeStream();
      void refreshSilently();
    });
    eventSource.addEventListener("error", () => {
      closeStream();
      if (disposed || !job.value || isTerminal(job.value)) return;
      streamState.value = "polling";
      scheduleStreamRetry();
    });
  } catch {
    streamState.value = "polling";
    scheduleStreamRetry();
  }
}

function scheduleStreamRetry() {
  if (sseRetryTimer || disposed) return;
  sseRetryTimer = window.setTimeout(() => {
    sseRetryTimer = undefined;
    if (!disposed && job.value && !isTerminal(job.value) && !source) startStream();
  }, 12_000);
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = window.setInterval(() => void refreshSilently(), 3_500);
}

async function refreshSilently() {
  if (pollBusy || disposed || !job.value || isTerminal(job.value)) return;
  pollBusy = true;
  try {
    const state = await api.getJob(props.jobId);
    applyState(state);
  } catch {
    // Polling is a fallback; transient failures should not replace the page.
  } finally {
    pollBusy = false;
  }
}

function closeStream() {
  source?.close();
  source = null;
}

function stopStreaming() {
  closeStream();
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = undefined;
  }
  if (sseRetryTimer) {
    window.clearTimeout(sseRetryTimer);
    sseRetryTimer = undefined;
  }
}

async function requestCancel() {
  if (!job.value || !isActive(job.value) || cancelling.value) return;
  if (!window.confirm("Cancel this workflow? Partial results will remain available.")) return;
  cancelling.value = true;
  actionError.value = "";
  try {
    const state = await api.cancelJob(job.value.id);
    applyState(state);
  } catch (err) {
    actionError.value = handleApiError(err);
  } finally {
    cancelling.value = false;
  }
}

async function deleteJob() {
  if (!job.value || !isTerminal(job.value) || deleting.value) return;
  if (
    !window.confirm(
      "Delete this workflow and its generated audio, text and subtitle files? The original PDF is kept until retention cleanup.",
    )
  ) {
    return;
  }
  deleting.value = true;
  actionError.value = "";
  try {
    await api.deleteJob(job.value.id);
    stopStreaming();
    emit("deleted");
  } catch (err) {
    actionError.value = handleApiError(err);
    deleting.value = false;
  }
}

async function copyLink() {
  if (!job.value) return;
  copying.value = true;
  const url = `${window.location.origin}${window.location.pathname}#/jobs/${job.value.id}`;
  const copied = await copyToClipboard(url);
  copyNotice.value = copied ? "Workflow link copied." : "Could not copy the link.";
  copying.value = false;
  if (noticeTimer) window.clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => {
    copyNotice.value = "";
  }, 3000);
}

function refresh() {
  void loadJob(false);
}
</script>

<template>
  <div class="job-view">
    <div v-if="loading" class="stack">
      <div class="card panel loading-panel">
        <div class="loading-mark"><span class="spinner large"></span></div>
        <div>
          <h2>Opening workflow…</h2>
          <p class="muted">Reading local job state and connecting to progress events.</p>
        </div>
      </div>
      <div class="card panel skeleton-panel">
        <span></span><span></span><span></span><span></span>
      </div>
    </div>

    <div v-else-if="error && !job" class="card panel error-panel">
      <div class="error-icon"><Icon name="alert" :size="26" /></div>
      <div class="grow">
        <h2>Workflow unavailable</h2>
        <p class="soft">{{ error }}</p>
        <p class="tiny muted">
          It may have expired under the configured retention policy, or the gateway may be offline.
        </p>
      </div>
      <div class="row wrap">
        <button class="btn ghost" type="button" @click="emit('open-library')">
          <Icon name="arrow-left" :size="16" /> Library
        </button>
        <button class="btn primary" type="button" @click="refresh">
          <Icon name="refresh" :size="16" /> Retry
        </button>
      </div>
    </div>

    <template v-else-if="job">
      <div class="job-topbar rise">
        <button class="back-link" type="button" @click="emit('open-library')">
          <Icon name="arrow-left" :size="15" /> All workflows
        </button>
        <div class="row wrap topbar-actions">
          <span class="stream-state" :class="streamState">
            <span class="status-dot" :class="{ pulse: !isTerminal(job) }"></span>
            {{ streamLabel }}
          </span>
          <span class="tiny muted mono job-id">#{{ job.id.slice(0, 8) }}</span>
          <button class="icon-btn small" type="button" title="Refresh workflow" aria-label="Refresh workflow" @click="refresh">
            <Icon name="refresh" :size="15" />
          </button>
        </div>
      </div>

      <section class="card panel job-hero rise">
        <div class="job-heading">
          <div class="grow">
            <div class="row wrap hero-badges">
              <span class="badge" :class="tone">
                <span v-if="isActive(job)" class="status-dot pulse"></span>
                {{ statusLabel(job.status) }}
              </span>
              <span v-for="label in outputBadges" :key="label" class="badge teal">{{ label }}</span>
              <span v-if="job.source.ocr_enabled" class="badge amber"><Icon name="scan" :size="12" /> OCR</span>
            </div>
            <h1>{{ job.title }}</h1>
            <div class="job-meta">
              <span><Icon name="file" :size="14" /> {{ sourceLabel(job) }}</span>
              <span><Icon name="clock" :size="14" /> {{ formatDate(job.created_at) }}</span>
              <span v-if="job.languages?.translation_target"><Icon name="translate" :size="14" /> {{ job.languages.translation_target }}</span>
            </div>
          </div>
          <div class="row wrap hero-actions">
            <button class="btn ghost" type="button" :disabled="copying" @click="copyLink">
              <Icon name="link" :size="16" /> {{ copying ? "Copying…" : "Copy link" }}
            </button>
            <button class="btn ghost" type="button" @click="emit('new-workflow')">
              <Icon name="plus" :size="16" /> New
            </button>
            <button
              v-if="isTerminal(job)"
              class="btn danger"
              type="button"
              :disabled="deleting"
              @click="deleteJob"
            >
              <span v-if="deleting" class="spinner"></span>
              <Icon v-else name="trash" :size="16" /> {{ deleting ? "Deleting…" : "Delete" }}
            </button>
          </div>
        </div>
        <div v-if="copyNotice" class="alert success compact" role="status">
          <Icon name="check" :size="16" /><span>{{ copyNotice }}</span>
        </div>
      </section>

      <div v-if="actionError" class="alert error" role="alert">
        <Icon name="alert" :size="18" /><span>{{ actionError }}</span>
      </div>

      <div v-if="job.error" class="card panel failure-panel rise">
        <div class="failure-head">
          <span class="failure-icon"><Icon name="alert" :size="22" /></span>
          <div class="grow">
            <span class="side-kicker">Failure report</span>
            <h2>{{ job.error.code }}</h2>
            <p>{{ job.error.message }}</p>
          </div>
        </div>
        <dl v-if="errorDetails.length" class="failure-details">
          <div v-for="[key, value] in errorDetails" :key="key">
            <dt>{{ key }}</dt>
            <dd class="mono">{{ value }}</dd>
          </div>
        </dl>
        <div class="row wrap failure-actions">
          <button class="btn primary" type="button" @click="emit('new-workflow')">
            <Icon name="plus" :size="16" /> Start a new workflow
          </button>
          <button class="btn ghost" type="button" @click="emit('open-library')">
            <Icon name="library" :size="16" /> Back to library
          </button>
        </div>
      </div>

      <JobProgress
        :job="job"
        :cancelling="cancelling"
        @cancel="requestCancel"
      />

      <JobResults v-if="hasResults" :job="job" @updated="refresh" />
    </template>
  </div>
</template>

<style scoped>
.job-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.job-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 0 3px 2px;
  flex-wrap: wrap;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  padding: 4px 0;
}

.back-link:hover {
  color: var(--text);
}

.topbar-actions {
  gap: 9px;
}

.stream-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--muted);
  font-weight: 600;
}

.stream-state.live {
  color: var(--teal);
}

.stream-state.polling {
  color: var(--amber);
}

.job-id {
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
}

.job-hero {
  background:
    radial-gradient(circle at 100% 0, var(--accent-soft), transparent 45%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent 60%),
    var(--surface);
}

.job-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}

.hero-badges {
  gap: 7px;
  margin-bottom: 10px;
}

.job-heading h1 {
  font-size: clamp(26px, 3.5vw, 38px);
  margin-bottom: 10px;
  letter-spacing: -0.035em;
  line-height: 1.08;
  overflow-wrap: anywhere;
}

.job-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 7px 15px;
  color: var(--muted);
  font-size: 12.5px;
}

.job-meta span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero-actions {
  gap: 8px;
}

.alert.compact {
  padding: 9px 12px;
  font-size: 12.5px;
  margin-top: 12px;
}

.failure-panel {
  border-color: rgba(255, 124, 138, 0.28);
  background:
    radial-gradient(circle at 100% 0, var(--danger-soft), transparent 48%),
    var(--surface);
}

.failure-head {
  display: flex;
  gap: 13px;
  align-items: flex-start;
}

.failure-icon {
  width: 44px;
  height: 44px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: var(--danger-soft);
  color: var(--danger);
}

.failure-head h2 {
  margin: 1px 0 5px;
  font-size: 18px;
}

.failure-head p {
  margin: 0;
  color: var(--text-soft);
  font-size: 13.5px;
}

.side-kicker {
  display: inline-block;
  color: var(--danger);
  font-size: 10.5px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.failure-details {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 15px 0 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-soft);
}

.failure-details > div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: 12px;
}

.failure-details dt {
  color: var(--muted);
  text-transform: capitalize;
}

.failure-details dd {
  margin: 0;
  color: var(--text-soft);
  text-align: right;
  word-break: break-word;
}

.failure-actions {
  margin-top: 15px;
  gap: 8px;
}

.loading-panel {
  display: flex;
  align-items: center;
  gap: 16px;
}

.loading-mark {
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: var(--accent-soft);
  color: var(--accent);
  flex: 0 0 auto;
}

.loading-panel h2 {
  font-size: 19px;
  margin-bottom: 3px;
}

.loading-panel p {
  margin: 0;
}

.skeleton-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.skeleton-panel span {
  height: 13px;
  border-radius: 5px;
  background: linear-gradient(100deg, var(--surface-2) 30%, var(--surface-3) 50%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: shimmer 1.4s linear infinite;
}

.skeleton-panel span:nth-child(2) {
  width: 85%;
}
.skeleton-panel span:nth-child(3) {
  width: 92%;
}
.skeleton-panel span:nth-child(4) {
  width: 65%;
}

.error-panel {
  display: flex;
  align-items: flex-start;
  gap: 15px;
  flex-wrap: wrap;
}

.error-icon {
  width: 50px;
  height: 50px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: var(--danger-soft);
  color: var(--danger);
  flex: 0 0 auto;
}

.error-panel h2 {
  font-size: 20px;
  margin-bottom: 4px;
}

.error-panel p:last-child {
  margin: 4px 0 0;
}

@keyframes shimmer {
  to {
    background-position: -220% 0;
  }
}

@media (max-width: 720px) {
  .job-heading {
    gap: 18px;
  }
  .hero-actions {
    width: 100%;
  }
  .hero-actions .btn {
    flex: 1;
  }
  .failure-details > div {
    flex-direction: column;
    gap: 2px;
  }
  .failure-details dd {
    text-align: left;
  }
}
</style>
