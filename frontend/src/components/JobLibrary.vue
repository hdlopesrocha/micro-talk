<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, handleApiError } from "../api/client";
import type { JobState, JobStatus } from "../api/types";
import { formatDuration, statusLabel } from "../utils/format";
import {
  formatRelative,
  isActive,
  isTerminal,
  outputLabels,
  sourceLabel,
  statusTone,
} from "../utils/jobs";
import Icon from "./Icon.vue";

const emit = defineEmits<{
  open: [jobId: string];
  create: [];
}>();

type Filter = "" | JobStatus;
const filters: Array<{ value: Filter; label: string }> = [
  { value: "", label: "All" },
  { value: "running", label: "Running" },
  { value: "queued", label: "Queued" },
  { value: "completed", label: "Complete" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

const jobs = ref<JobState[]>([]);
const total = ref(0);
const filter = ref<Filter>("");
const offset = ref(0);
const limit = 12;
const loading = ref(true);
const refreshing = ref(false);
const error = ref("");
const deletingId = ref("");
let pollTimer: number | undefined;
let requestId = 0;

const activeOnPage = computed(() => jobs.value.some((job) => isActive(job)));
const page = computed(() => Math.floor(offset.value / limit) + 1);
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit)));
const firstShown = computed(() => (total.value ? offset.value + 1 : 0));
const lastShown = computed(() => Math.min(offset.value + jobs.value.length, total.value));

watch(filter, () => {
  offset.value = 0;
  void load();
});

onMounted(() => void load());
onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer);
});

async function load(silent = false) {
  const currentRequest = ++requestId;
  if (silent) refreshing.value = true;
  else loading.value = true;
  error.value = "";
  try {
    const result = await api.listJobs({
      limit,
      offset: offset.value,
      status: filter.value || undefined,
    });
    if (currentRequest !== requestId) return;
    jobs.value = result.jobs || [];
    total.value = result.total || 0;
    if (offset.value > 0 && !jobs.value.length && total.value > 0) {
      offset.value = Math.max(0, Math.floor((total.value - 1) / limit) * limit);
      await load(silent);
      return;
    }
    syncPolling();
  } catch (err) {
    if (currentRequest === requestId) error.value = handleApiError(err);
  } finally {
    if (currentRequest === requestId) {
      loading.value = false;
      refreshing.value = false;
    }
  }
}

function syncPolling() {
  if (activeOnPage.value && !pollTimer) {
    pollTimer = window.setInterval(() => void load(true), 5_000);
  } else if (!activeOnPage.value && pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = undefined;
  }
}

function openJob(job: JobState) {
  emit("open", job.id);
}

function goPage(delta: number) {
  const next = Math.min(Math.max(0, offset.value + delta * limit), Math.max(0, (pageCount.value - 1) * limit));
  if (next === offset.value) return;
  offset.value = next;
  void load();
}

async function removeJob(job: JobState) {
  if (!isTerminal(job) || deletingId.value) return;
  if (!window.confirm(`Delete “${job.title}” and its generated files? The source PDF is kept.`)) {
    return;
  }
  deletingId.value = job.id;
  error.value = "";
  try {
    await api.deleteJob(job.id);
    await load();
  } catch (err) {
    error.value = handleApiError(err);
  } finally {
    deletingId.value = "";
  }
}

function durationFor(job: JobState): string {
  const seconds =
    job.artifacts.audio?.duration_seconds ||
    Number(job.stats?.audio_seconds || job.stats?.speech_seconds || 0);
  return seconds ? formatDuration(seconds) : "";
}
</script>

<template>
  <div class="library">
    <section class="card panel library-hero rise">
      <div class="grow">
        <span class="kicker"><Icon name="library" :size="13" /> Local library</span>
        <h1>Workflows on this machine</h1>
        <p>
          Every workflow is processed and stored locally. Open a result to listen, edit text or
          download the bundle.
        </p>
      </div>
      <div class="row wrap hero-actions">
        <button class="btn ghost" type="button" :disabled="refreshing" @click="load(true)">
          <span v-if="refreshing" class="spinner"></span>
          <Icon v-else name="refresh" :size="16" /> Refresh
        </button>
        <button class="btn primary" type="button" @click="emit('create')">
          <Icon name="plus" :size="16" /> New workflow
        </button>
      </div>
    </section>

    <div class="library-toolbar">
      <div class="tabs" role="group" aria-label="Filter workflows">
        <button
          v-for="item in filters"
          :key="item.value"
          type="button"
          :class="{ active: filter === item.value }"
          @click="filter = item.value"
        >
          {{ item.label }}
        </button>
      </div>
      <span class="tiny muted">
        {{ total.toLocaleString() }} workflow{{ total === 1 ? "" : "s" }}
        <span v-if="activeOnPage" class="live-note"><span class="status-dot pulse"></span> updating live</span>
      </span>
    </div>

    <div v-if="error" class="alert error" role="alert">
      <Icon name="alert" :size="18" />
      <span class="grow">{{ error }}</span>
      <button class="btn ghost small" type="button" @click="load()">Retry</button>
    </div>

    <div v-if="loading" class="job-grid">
      <div v-for="index in 4" :key="index" class="card panel job-skeleton">
        <span class="skeleton-line short"></span>
        <span class="skeleton-line"></span>
        <span class="skeleton-line medium"></span>
        <span class="skeleton-line short"></span>
      </div>
    </div>

    <div v-else-if="!jobs.length && !error" class="card panel empty-state">
      <span class="empty-icon"><Icon name="library" :size="28" /></span>
      <h2>{{ filter ? "No workflows match this filter" : "Your library is empty" }}</h2>
      <p class="muted">
        {{
          filter
            ? "Try another status or show every workflow."
            : "Create your first local document workflow to see it here."
        }}
      </p>
      <button v-if="!filter" class="btn primary" type="button" @click="emit('create')">
        <Icon name="plus" :size="16" /> Create a workflow
      </button>
      <button v-else class="btn ghost" type="button" @click="filter = ''">Show all</button>
    </div>

    <div v-else class="job-grid">
      <article v-for="job in jobs" :key="job.id" class="card job-card">
        <button class="job-open" type="button" @click="openJob(job)">
          <span class="job-card-top">
            <span class="badge" :class="statusTone(job.status)">
              <span v-if="isActive(job)" class="status-dot pulse"></span>
              {{ statusLabel(job.status) }}
            </span>
            <span class="tiny muted">{{ formatRelative(job.created_at) }}</span>
          </span>

          <strong class="job-title">{{ job.title }}</strong>
          <span class="job-source tiny muted">
            <Icon name="file" :size="13" /> {{ sourceLabel(job) }}
          </span>

          <span v-if="isActive(job)" class="job-progress">
            <span class="progress thin"><span :style="{ width: `${job.progress}%` }"></span></span>
            <span class="tiny muted progress-label">{{ job.progress }}% · {{ job.message }}</span>
          </span>
          <span v-else-if="job.status === 'failed'" class="job-failure tiny">
            {{ job.error?.message || job.message }}
          </span>
          <span v-else-if="job.status === 'cancelled'" class="tiny muted">
            {{ job.message }}
          </span>
          <span v-else class="tiny muted job-message">{{ job.message }}</span>

          <span class="job-card-foot">
            <span class="row wrap output-badges">
              <span v-for="label in outputLabels(job.artifacts)" :key="label" class="badge">
                {{ label }}
              </span>
              <span v-if="durationFor(job)" class="badge teal">
                <Icon name="volume" :size="11" /> {{ durationFor(job) }}
              </span>
              <span v-if="!outputLabels(job.artifacts).length" class="tiny muted">No artifacts yet</span>
            </span>
            <span class="open-label">Open <Icon name="arrow-right" :size="14" /></span>
          </span>
        </button>

        <button
          v-if="isTerminal(job)"
          class="delete-btn"
          type="button"
          :disabled="deletingId === job.id"
          :aria-label="`Delete ${job.title}`"
          title="Delete workflow"
          @click="removeJob(job)"
        >
          <span v-if="deletingId === job.id" class="spinner"></span>
          <Icon v-else name="trash" :size="15" />
        </button>
      </article>
    </div>

    <div v-if="!loading && total > limit" class="pager">
      <span class="tiny muted">{{ firstShown }}–{{ lastShown }} of {{ total }}</span>
      <div class="row">
        <button class="btn ghost small" type="button" :disabled="offset === 0" @click="goPage(-1)">
          <Icon name="arrow-left" :size="14" /> Previous
        </button>
        <span class="tiny mono muted">Page {{ page }} / {{ pageCount }}</span>
        <button class="btn ghost small" type="button" :disabled="offset + limit >= total" @click="goPage(1)">
          Next <Icon name="arrow-right" :size="14" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.library {
  display: flex;
  flex-direction: column;
  gap: 17px;
}

.library-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
  background:
    radial-gradient(circle at 100% 0, var(--teal-soft), transparent 44%),
    var(--surface);
}

.kicker {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--teal);
  font-size: 10.5px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 9px;
}

.library-hero h1 {
  font-size: clamp(27px, 3.7vw, 40px);
  letter-spacing: -0.04em;
  margin-bottom: 7px;
}

.library-hero p {
  max-width: 650px;
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}

.hero-actions {
  gap: 8px;
}

.library-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.live-note {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--teal);
  margin-left: 8px;
}

.job-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 13px;
}

.job-card {
  position: relative;
  overflow: hidden;
  transition: border-color 0.16s ease, transform 0.16s ease;
}

.job-card:hover {
  border-color: var(--border-strong);
  transform: translateY(-2px);
}

.job-open {
  display: flex;
  flex-direction: column;
  gap: 7px;
  width: 100%;
  border: 0;
  background: transparent;
  color: var(--text);
  text-align: left;
  padding: 17px 48px 16px 17px;
}

.job-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.job-title {
  font-size: 16px;
  letter-spacing: -0.02em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-source {
  display: flex;
  align-items: center;
  gap: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-progress,
.job-message,
.job-failure {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 2px 0;
  min-height: 32px;
  justify-content: center;
}

.job-failure {
  color: var(--danger);
  font-size: 12px;
  line-height: 1.4;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.job-message {
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 12px;
  line-height: 1.4;
}

.progress-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}

.output-badges {
  gap: 4px;
}

.output-badges .badge {
  min-height: 21px;
  font-size: 10.5px;
  padding: 2px 7px;
}

.open-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 650;
  white-space: nowrap;
}

.delete-btn {
  position: absolute;
  top: 13px;
  right: 12px;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--faint);
  z-index: 2;
  transition: 0.15s ease;
}

.delete-btn:hover:not(:disabled) {
  color: var(--danger);
  background: var(--danger-soft);
  border-color: rgba(255, 124, 138, 0.25);
}

.delete-btn:disabled {
  opacity: 0.5;
  cursor: wait;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 4px 2px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 9px;
  padding: 58px 24px;
  text-align: center;
}

.empty-state h2 {
  font-size: 21px;
  margin: 3px 0 0;
}

.empty-state p {
  max-width: 400px;
  margin: 0 0 8px;
  font-size: 13.5px;
}

.empty-icon {
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: var(--accent-soft);
  color: var(--accent);
}

.job-skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 190px;
}

.skeleton-line {
  height: 12px;
  border-radius: 5px;
  width: 100%;
  background: linear-gradient(100deg, var(--surface-2) 30%, var(--surface-3) 50%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: shimmer 1.4s linear infinite;
}

.skeleton-line.short {
  width: 34%;
}
.skeleton-line.medium {
  width: 62%;
}

@keyframes shimmer {
  to {
    background-position: -220% 0;
  }
}

@media (max-width: 900px) {
  .job-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 620px) {
  .library-hero {
    align-items: flex-start;
  }
  .hero-actions {
    width: 100%;
  }
  .hero-actions .btn {
    flex: 1;
  }
  .job-open {
    padding: 15px 45px 14px 15px;
  }
  .pager {
    justify-content: center;
  }
  .pager .row {
    flex-wrap: wrap;
    justify-content: center;
  }
}
</style>
