<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api, handleApiError } from "../api/client";
import type { TextArtifact } from "../api/types";
import { copyToClipboard } from "../utils/clipboard";
import { countWords, formatDate } from "../utils/format";
import Icon from "./Icon.vue";

const props = defineProps<{
  jobId: string;
  kind: "extracted" | "translation" | "summary" | string;
  label: string;
  description?: string;
  artifact: TextArtifact;
}>();

const emit = defineEmits<{
  saved: [artifact: TextArtifact, warning: string | null];
  dirty: [value: boolean];
}>();

const loading = ref(true);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const warning = ref("");
const original = ref("");
const draft = ref("");
const savedAt = ref<number | null>(props.artifact.edited_at || null);
let noticeTimer: number | undefined;

const dirty = computed(() => draft.value !== original.value);
const words = computed(() => countWords(draft.value));

watch(dirty, (value) => emit("dirty", value));

watch(
  () => [props.jobId, props.kind] as const,
  () => void load(),
  { immediate: true },
);

async function load() {
  loading.value = true;
  error.value = "";
  warning.value = "";
  notice.value = "";
  try {
    const text = await api.jobText(props.jobId, props.kind);
    original.value = text;
    draft.value = text;
    savedAt.value = props.artifact.edited_at || null;
  } catch (err) {
    error.value = handleApiError(err);
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (saving.value || !dirty.value) return;
  if (!draft.value.trim()) {
    error.value = "The text result cannot be empty. Revert the change or keep at least one character.";
    return;
  }
  saving.value = true;
  error.value = "";
  warning.value = "";
  try {
    const result = await api.saveJobText(props.jobId, props.kind, draft.value);
    original.value = draft.value;
    savedAt.value = Date.now() / 1000;
    warning.value = result.warning || "";
    flash("Saved. The download now contains your edited text.");
    emit("saved", result.artifact as unknown as TextArtifact, result.warning || null);
  } catch (err) {
    error.value = handleApiError(err);
  } finally {
    saving.value = false;
  }
}

function revert() {
  draft.value = original.value;
  notice.value = "";
}

async function copy() {
  const copied = await copyToClipboard(draft.value);
  flash(copied ? "Copied to the clipboard." : "Could not access the clipboard.");
}

function flash(message: string) {
  notice.value = message;
  if (noticeTimer) window.clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => {
    notice.value = "";
  }, 3600);
}

function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    void save();
  }
}
</script>

<template>
  <div class="text-result">
    <div class="text-head">
      <div>
        <div class="row wrap">
          <strong>{{ label }}</strong>
          <span class="badge">{{ words.toLocaleString() }} words</span>
          <span v-if="dirty" class="badge amber">Unsaved changes</span>
        </div>
        <p v-if="description" class="tiny muted">{{ description }}</p>
      </div>
      <div class="row wrap text-actions">
        <button class="btn ghost small" type="button" :disabled="loading || !draft" @click="copy">
          <Icon name="copy" :size="14" /> Copy
        </button>
        <a
          class="btn ghost small"
          :href="api.downloadUrl(jobId, kind)"
          :download="artifact.filename"
        >
          <Icon name="download" :size="14" /> TXT
        </a>
      </div>
    </div>

    <div v-if="loading" class="skeleton-text" aria-label="Loading text result">
      <span></span><span></span><span></span><span></span><span></span>
    </div>

    <div v-else-if="error && !draft" class="alert error" role="alert">
      <Icon name="alert" :size="18" /><span>{{ error }}</span>
    </div>

    <template v-else>
      <textarea
        v-model="draft"
        class="textarea result-text"
        spellcheck="true"
        :aria-label="`Edit ${label}`"
        @keydown="onKeydown"
      ></textarea>

      <div class="text-foot">
        <div class="text-meta tiny muted">
          <span>{{ draft.length.toLocaleString() }} characters</span>
          <span aria-hidden="true">·</span>
          <span>{{ artifact.filename }}</span>
          <template v-if="savedAt">
            <span aria-hidden="true">·</span>
            <span>edited {{ formatDate(savedAt) }}</span>
          </template>
        </div>
        <div class="row">
          <button
            v-if="dirty"
            class="btn ghost small"
            type="button"
            :disabled="saving"
            @click="revert"
          >
            Revert
          </button>
          <button class="btn primary small" type="button" :disabled="!dirty || saving" @click="save">
            <span v-if="saving" class="spinner"></span>
            <Icon v-else name="check" :size="15" />
            {{ saving ? "Saving…" : "Save changes" }}
          </button>
        </div>
      </div>

      <div v-if="notice" class="alert success compact" role="status">
        <Icon name="check" :size="17" /><span>{{ notice }}</span>
      </div>
      <div v-if="warning" class="alert warn compact" role="status">
        <Icon name="info" :size="17" /><span>{{ warning }}</span>
      </div>
      <div v-if="error" class="alert error compact" role="alert">
        <Icon name="alert" :size="17" /><span>{{ error }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.text-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.text-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.text-head p {
  margin: 4px 0 0;
}

.text-actions {
  gap: 7px;
}

.result-text {
  min-height: 400px;
  font-size: 14.5px;
  line-height: 1.72;
  font-family: var(--font-sans);
  tab-size: 4;
}

.text-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.text-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.alert.compact {
  padding: 10px 12px;
  font-size: 12.5px;
}

.skeleton-text {
  min-height: 400px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 15px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
}

.skeleton-text span {
  height: 12px;
  border-radius: 5px;
  background: linear-gradient(100deg, var(--surface-2) 30%, var(--surface-3) 50%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: shimmer 1.4s linear infinite;
}

.skeleton-text span:nth-child(2) {
  width: 92%;
}
.skeleton-text span:nth-child(3) {
  width: 97%;
}
.skeleton-text span:nth-child(4) {
  width: 78%;
}
.skeleton-text span:nth-child(5) {
  width: 85%;
}

@keyframes shimmer {
  to {
    background-position: -220% 0;
  }
}
</style>
