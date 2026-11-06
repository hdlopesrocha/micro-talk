<script setup lang="ts">
import { computed } from "vue";
import type { JobState } from "../api/types";
import {
  elapsedLabel,
  formatDuration,
  formatDate,
  statusLabel,
} from "../utils/format";
import { isActive, statusTone, stepIcon } from "../utils/jobs";
import Icon from "./Icon.vue";

const props = defineProps<{
  job: JobState;
  cancelling?: boolean;
}>();

defineEmits<{
  cancel: [];
}>();

const active = computed(() => isActive(props.job));
const tone = computed(() => statusTone(props.job.status));
const headline = computed(() => {
  switch (props.job.status) {
    case "queued":
      return "Waiting in the local queue";
    case "running":
      return "Processing locally";
    case "cancelling":
      return "Stopping at the next safe point";
    case "completed":
      return "Workflow complete";
    case "failed":
      return "Workflow failed";
    case "cancelled":
      return "Workflow cancelled";
    default:
      return props.job.status;
  }
});
const completedSteps = computed(
  () => props.job.steps.filter((step) => ["completed", "skipped"].includes(step.status)).length,
);
const currentStepLabel = computed(() => {
  const current = props.job.steps.find((step) => step.id === props.job.current_step);
  return current?.label || "";
});
const totalSeconds = computed(() =>
  Number(props.job.stats?.audio_seconds || props.job.stats?.speech_seconds || 0),
);

function stepTone(status: string): string {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "running") return "accent";
  if (status === "cancelled") return "amber";
  return "";
}
</script>

<template>
  <section class="card panel progress-panel">
    <div class="progress-top">
      <div class="progress-ring" :style="{ '--value': job.progress }">
        <span>{{ job.progress }}%</span>
      </div>
      <div class="grow progress-copy">
        <div class="row wrap progress-badges">
          <span class="badge" :class="tone">
            <span v-if="active" class="status-dot pulse"></span>
            {{ statusLabel(job.status) }}
          </span>
          <span v-if="currentStepLabel" class="badge accent">{{ currentStepLabel }}</span>
          <span class="tiny muted">{{ completedSteps }} / {{ job.steps.length }} stages</span>
        </div>
        <h2>{{ headline }}</h2>
        <p class="message">{{ job.message }}</p>
        <div class="progress">
          <span :style="{ width: `${job.progress}%` }"></span>
        </div>
        <div class="progress-meta tiny muted">
          <span><Icon name="clock" :size="13" /> Started {{ formatDate(job.started_at) }}</span>
          <span>Elapsed {{ elapsedLabel(job.started_at, job.finished_at) }}</span>
          <span v-if="totalSeconds > 0">Audio {{ formatDuration(totalSeconds) }}</span>
          <span>Updated {{ formatDate(job.updated_at) }}</span>
        </div>
      </div>
      <button
        v-if="active"
        class="btn danger cancel-btn"
        type="button"
        :disabled="cancelling || job.status === 'cancelling'"
        @click="$emit('cancel')"
      >
        <span v-if="cancelling || job.status === 'cancelling'" class="spinner"></span>
        <Icon v-else name="x" :size="16" />
        {{ job.status === "cancelling" ? "Cancelling…" : "Cancel" }}
      </button>
    </div>

    <div class="steps">
      <div
        v-for="(step, index) in job.steps"
        :key="step.id"
        class="step"
        :class="step.status"
      >
        <span class="step-rail" aria-hidden="true">
          <span class="step-dot"><Icon :name="stepIcon(step.status)" :size="14" /></span>
          <span v-if="index < job.steps.length - 1" class="step-line"></span>
        </span>
        <div class="step-body">
          <div class="row between wrap step-title">
            <strong>{{ step.label }}</strong>
            <span class="badge" :class="stepTone(step.status)">{{ step.status }}</span>
          </div>
          <p class="step-message">{{ step.message }}</p>
          <div v-if="step.status === 'running'" class="progress thin">
            <span :style="{ width: `${Math.max(4, step.progress)}%` }"></span>
          </div>
          <div v-if="step.error" class="tiny error-text">
            {{ step.error.message }}
          </div>
        </div>
      </div>
    </div>

    <div v-if="job.warnings.length" class="warnings">
      <div class="field-label"><Icon name="info" :size="14" /> Notes</div>
      <ul>
        <li v-for="(warning, index) in job.warnings" :key="index">{{ warning }}</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.progress-panel {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.progress-top {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  flex-wrap: wrap;
}

.progress-copy {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.progress-badges {
  gap: 8px;
}

.progress-copy h2 {
  margin: 0;
  font-size: 24px;
}

.message {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}

.progress-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 2px;
}

.progress-meta span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.cancel-btn {
  align-self: center;
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.step {
  display: flex;
  gap: 13px;
  min-height: 58px;
}

.step-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 28px;
  flex: 0 0 auto;
}

.step-dot {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: var(--surface-2);
  color: var(--faint);
  flex: 0 0 auto;
}

.step.completed .step-dot {
  border-color: rgba(85, 215, 161, 0.35);
  background: var(--success-soft);
  color: var(--success);
}

.step.running .step-dot {
  border-color: rgba(123, 156, 255, 0.42);
  background: var(--accent-soft);
  color: var(--accent);
  animation: pulse 1.8s ease-in-out infinite;
}

.step.failed .step-dot {
  border-color: rgba(255, 124, 138, 0.35);
  background: var(--danger-soft);
  color: var(--danger);
}

.step.cancelled .step-dot {
  border-color: rgba(246, 189, 114, 0.35);
  background: var(--amber-soft);
  color: var(--amber);
}

.step-line {
  flex: 1;
  width: 1px;
  min-height: 12px;
  background: var(--border);
}

.step-body {
  flex: 1;
  min-width: 0;
  padding-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.step-title {
  gap: 8px;
}

.step-title strong {
  font-size: 13.5px;
  color: var(--text-soft);
}

.step.completed .step-title strong,
.step.running .step-title strong {
  color: var(--text);
}

.step-message {
  margin: 0;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.45;
}

.step .badge {
  text-transform: capitalize;
  min-height: 21px;
  font-size: 10.5px;
  padding: 2px 7px;
}

.error-text {
  color: var(--danger);
  font-size: 12px;
}

.warnings {
  border-top: 1px solid var(--border);
  padding-top: 15px;
}

.warnings .field-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 7px;
}

.warnings ul {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.55;
}

@media (max-width: 620px) {
  .progress-top {
    gap: 13px;
  }
  .progress-ring {
    width: 82px;
    height: 82px;
  }
  .progress-ring > span {
    font-size: 17px;
  }
  .progress-copy h2 {
    font-size: 20px;
  }
  .cancel-btn {
    width: 100%;
  }
}
</style>
