<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api, handleApiError } from "../api/client";
import type { Cue, JobState, TextArtifact } from "../api/types";
import { formatBytes, formatDate, formatDuration, languageLabel } from "../utils/format";
import AudioPlayer from "./AudioPlayer.vue";
import Icon from "./Icon.vue";
import TextResult from "./TextResult.vue";

const props = defineProps<{
  job: JobState;
}>();

const emit = defineEmits<{
  updated: [job: JobState];
}>();

interface TextKind {
  kind: "extracted" | "translation" | "summary";
  label: string;
  description: string;
  icon: string;
}

const textKinds: TextKind[] = [
  {
    kind: "translation",
    label: "Translation",
    description: "Paragraphs and page markers are preserved. Edits update the TXT download.",
    icon: "translate",
  },
  {
    kind: "summary",
    label: "Summary",
    description: "Local T5 summary. Editing changes the downloadable text only.",
    icon: "sparkles",
  },
  {
    kind: "extracted",
    label: "Extracted text",
    description: "The source text after PDF extraction or OCR. Edits update the TXT download.",
    icon: "file-text",
  },
];

const cues = ref<Cue[]>([]);
const cuesLoading = ref(false);
const cuesError = ref("");
const selectedKind = ref<TextKind["kind"]>("extracted");

const availableTextKinds = computed(() =>
  textKinds.filter((item) => !!props.job.artifacts[item.kind]?.available),
);
const selectedText = computed(() =>
  availableTextKinds.value.find((item) => item.kind === selectedKind.value),
);
const currentArtifact = computed<TextArtifact | null>(() => {
  const artifact = props.job.artifacts[selectedKind.value];
  return (artifact as TextArtifact) || null;
});
const audioArtifact = computed(() => props.job.artifacts.audio || null);
const wavArtifact = computed(() => props.job.artifacts.wav || null);
const audioKind = computed<"audio" | "wav" | null>(() => {
  if (audioArtifact.value?.available) return "audio";
  if (wavArtifact.value?.available) return "wav";
  return null;
});
const audioSource = computed(() =>
  audioKind.value ? api.downloadUrl(props.job.id, audioKind.value, true) : "",
);
const audioDuration = computed(
  () => audioArtifact.value?.duration_seconds || wavArtifact.value?.duration_seconds || null,
);
const audioTitle = computed(() => {
  const voice = props.job.tts?.voice_name || props.job.config?.tts?.voice;
  return voice ? `${props.job.title} · ${voice}` : props.job.title;
});
const zipArtifact = computed(() => props.job.artifacts.zip || null);
const subtitleArtifact = computed(() => props.job.artifacts.subtitles || null);
const hasResults = computed(
  () =>
    !!availableTextKinds.value.length ||
    !!audioArtifact.value?.available ||
    !!wavArtifact.value?.available ||
    !!zipArtifact.value?.available,
);

const downloads = computed(() => {
  const items: Array<{
    kind: string;
    label: string;
    filename: string;
    meta: string;
    icon: string;
  }> = [];
  const artifacts = props.job.artifacts;
  if (artifacts.translation?.available) {
    items.push({
      kind: "translation",
      label: "Translation",
      filename: artifacts.translation.filename,
      meta: `${formatBytes(artifacts.translation.size_bytes)} · ${(artifacts.translation.words || 0).toLocaleString()} words`,
      icon: "translate",
    });
  }
  if (artifacts.summary?.available) {
    items.push({
      kind: "summary",
      label: "Summary",
      filename: artifacts.summary.filename,
      meta: `${formatBytes(artifacts.summary.size_bytes)} · ${(artifacts.summary.words || 0).toLocaleString()} words`,
      icon: "sparkles",
    });
  }
  if (artifacts.extracted?.available) {
    items.push({
      kind: "extracted",
      label: "Extracted text",
      filename: artifacts.extracted.filename,
      meta: `${formatBytes(artifacts.extracted.size_bytes)} · ${(artifacts.extracted.words || 0).toLocaleString()} words`,
      icon: "file-text",
    });
  }
  if (audioArtifact.value?.available) {
    items.push({
      kind: "audio",
      label: `Audio (${String(audioArtifact.value.format || "mp3").toUpperCase()})`,
      filename: audioArtifact.value.filename,
      meta: [
        audioArtifact.value.size_bytes ? formatBytes(audioArtifact.value.size_bytes) : "",
        audioArtifact.value.duration_seconds
          ? formatDuration(audioArtifact.value.duration_seconds)
          : "",
      ]
        .filter(Boolean)
        .join(" · "),
      icon: "volume",
    });
  }
  if (
    wavArtifact.value?.available &&
    wavArtifact.value.path !== audioArtifact.value?.path
  ) {
    items.push({
      kind: "wav",
      label: "Master WAV",
      filename: wavArtifact.value.filename,
      meta: wavArtifact.value.duration_seconds
        ? formatDuration(wavArtifact.value.duration_seconds)
        : "Uncompressed",
      icon: "volume",
    });
  }
  if (artifacts.subtitles?.available) {
    items.push({
      kind: "srt",
      label: "Subtitles (SRT)",
      filename: artifacts.subtitles.filename,
      meta: `${formatBytes(artifacts.subtitles.size_bytes)} · ${artifacts.subtitles.segments || 0} cues`,
      icon: "captions",
    });
  }
  if (artifacts.captions?.available) {
    items.push({
      kind: "vtt",
      label: "WebVTT captions",
      filename: artifacts.captions.filename,
      meta: `${formatBytes(artifacts.captions.size_bytes)} · browser captions`,
      icon: "captions",
    });
  }
  return items;
});

const statItems = computed(() => {
  const stats = props.job.stats || {};
  const items: Array<{ label: string; value: string }> = [];
  const push = (label: string, value: number | string | null | undefined, suffix = "") => {
    if (value === null || value === undefined || value === "") return;
    const numeric = typeof value === "number" ? value : Number(value);
    items.push({
      label,
      value: Number.isFinite(numeric)
        ? `${numeric.toLocaleString()}${suffix}`
        : `${String(value)}${suffix}`,
    });
  };
  push("Source words", stats.extracted_words);
  push("Translated words", stats.translated_words);
  push("Summary words", stats.summary_words);
  push("Speech", stats.audio_seconds || stats.speech_seconds, "s");
  push("Cues", stats.subtitle_segments);
  push("Summary chunks", stats.summary_chunks);
  return items;
});

const contentLanguage = computed(() =>
  languageLabel(String(props.job.languages?.translation_target || props.job.languages?.content || "")),
);
const summaryLanguage = computed(() => languageLabel(String(props.job.languages?.summary || "")));
const timingMethod = computed(
  () =>
    props.job.artifacts.subtitles?.timing_method ||
    (props.job.stats?.subtitle_timing_method as string | undefined) ||
    "",
);

watch(
  () => props.job.id,
  () => {
    selectedKind.value = defaultTextKind();
    cues.value = [];
    cuesError.value = "";
  },
  { immediate: true },
);

watch(
  () => availableTextKinds.value.map((item) => item.kind),
  () => {
    if (!availableTextKinds.value.some((item) => item.kind === selectedKind.value)) {
      selectedKind.value = defaultTextKind();
    }
  },
  { immediate: true },
);

watch(
  () => [props.job.id, !!audioKind.value, !!subtitleArtifact.value?.available] as const,
  () => void loadCues(),
  { immediate: true },
);

function defaultTextKind(): TextKind["kind"] {
  if (props.job.config?.tts?.text_source === "summary" && props.job.artifacts.summary?.available) {
    return "summary";
  }
  if (props.job.artifacts.translation?.available) return "translation";
  if (props.job.artifacts.summary?.available) return "summary";
  return "extracted";
}

async function loadCues() {
  cues.value = [];
  cuesError.value = "";
  if (!subtitleArtifact.value?.available) return;
  cuesLoading.value = true;
  try {
    const result = await api.cues(props.job.id);
    cues.value = result.segments || [];
  } catch (error) {
    cuesError.value = handleApiError(error);
  } finally {
    cuesLoading.value = false;
  }
}

function onSaved(artifact: TextArtifact, warning: string | null) {
  if (!warning) {
    // The warning is rendered locally, but the job still needs a refresh so the
    // artifact metadata and any new warning in the sidebar stay in sync.
  }
  emit("updated", props.job);
  void artifact;
}
</script>

<template>
  <div v-if="hasResults" class="results stack">
    <section class="card panel">
      <div class="section-heading results-heading">
        <div>
          <span class="side-kicker"><Icon name="layers" :size="13" /> Output</span>
          <h2>Results</h2>
          <p>Preview the audio, edit text results and download individual files.</p>
        </div>
        <a v-if="zipArtifact?.available" class="btn primary" :href="api.downloadUrl(job.id, 'zip')" :download="zipArtifact.filename">
          <Icon name="download" :size="17" /> Download ZIP
          <span class="zip-size">{{ formatBytes(zipArtifact.size_bytes) }}</span>
        </a>
      </div>

      <div v-if="audioKind" class="audio-block">
        <AudioPlayer
          :src="audioSource"
          :cues="cues"
          :title="audioTitle"
          :format="audioArtifact?.format || wavArtifact?.format || ''"
          :duration-hint="audioDuration"
        />
        <div v-if="cuesLoading" class="tiny muted row cue-loading">
          <span class="spinner"></span> Loading subtitle timeline…
        </div>
        <div v-else-if="cuesError" class="alert warn compact">
          <Icon name="alert" :size="17" />
          <span>The audio is ready, but the synchronized subtitle cues could not be loaded. {{ cuesError }}</span>
        </div>
      </div>

      <div v-if="availableTextKinds.length" class="text-section">
        <div class="row between wrap text-section-head">
          <div>
            <span class="field-label">Text results</span>
            <p class="tiny muted">Select a result, edit it and save. Downloads update immediately.</p>
          </div>
          <div class="tabs">
            <button
              v-for="item in availableTextKinds"
              :key="item.kind"
              type="button"
              :class="{ active: selectedKind === item.kind }"
              @click="selectedKind = item.kind"
            >
              <Icon :name="item.icon" :size="14" />
              {{ item.label }}
            </button>
          </div>
        </div>
        <TextResult
          v-if="selectedText && currentArtifact"
          :key="`${job.id}-${selectedKind}`"
          :job-id="job.id"
          :kind="selectedKind"
          :label="selectedText.label"
          :description="selectedText.description"
          :artifact="currentArtifact"
          @saved="onSaved"
        />
      </div>

      <div v-if="!audioKind && !availableTextKinds.length" class="empty compact-empty">
        <Icon name="clock" :size="26" />
        <div>
          <h3>Results are still being prepared</h3>
          <p class="tiny muted">This panel updates automatically as each stage completes.</p>
        </div>
      </div>
    </section>

    <div class="results-grid">
      <section class="card panel">
        <div class="section-heading compact-heading">
          <div>
            <h2>Downloads</h2>
            <p>Every available artifact for this workflow.</p>
          </div>
        </div>
        <div class="download-list">
          <a
            v-for="item in downloads"
            :key="item.kind"
            class="download-item"
            :href="api.downloadUrl(job.id, item.kind)"
            :download="item.filename"
          >
            <span class="download-icon"><Icon :name="item.icon" :size="18" /></span>
            <span class="grow">
              <strong>{{ item.label }}</strong>
              <small>{{ item.filename }}<template v-if="item.meta"> · {{ item.meta }}</template></small>
            </span>
            <Icon name="download" :size="17" class="download-arrow" />
          </a>
          <div v-if="!downloads.length" class="tiny muted no-downloads">
            Files will appear here as soon as each stage finishes.
          </div>
        </div>
        <a
          v-if="zipArtifact?.available"
          class="btn wide zip-button"
          :href="api.downloadUrl(job.id, 'zip')"
          :download="zipArtifact.filename"
        >
          <Icon name="download" :size="16" /> Complete bundle · {{ formatBytes(zipArtifact.size_bytes) }}
        </a>
      </section>

      <section class="card panel details-card">
        <div class="section-heading compact-heading">
          <div>
            <h2>Workflow details</h2>
            <p>Generated locally and stored on this machine.</p>
          </div>
        </div>
        <dl class="details">
          <div><dt>Created</dt><dd>{{ formatDate(job.created_at) }}</dd></div>
          <div><dt>Finished</dt><dd>{{ formatDate(job.finished_at) }}</dd></div>
          <div v-if="job.source.filename"><dt>Source</dt><dd class="truncate" :title="job.source.filename">{{ job.source.filename }}</dd></div>
          <div v-if="job.source.selected_pages?.length"><dt>Pages</dt><dd>{{ job.source.selected_pages.length }} selected</dd></div>
          <div v-if="job.source.ocr_enabled"><dt>OCR</dt><dd>Enabled</dd></div>
          <div v-if="job.source.ignore_notes"><dt>Notes</dt><dd>Ignored</dd></div>
          <div v-if="job.source.ignore_figure_descriptions"><dt>Figure descriptions</dt><dd>Ignored</dd></div>
          <div><dt>Text language</dt><dd>{{ contentLanguage }}</dd></div>
          <div v-if="job.artifacts.summary"><dt>Summary language</dt><dd>{{ summaryLanguage }}</dd></div>
          <div v-if="job.config?.tts?.voice"><dt>Voice</dt><dd>{{ job.tts?.voice_name || job.config.tts.voice }}</dd></div>
          <div v-if="timingMethod"><dt>Subtitle timing</dt><dd>{{ timingMethod }}</dd></div>
          <div v-if="subtitleArtifact?.segments"><dt>Subtitle cues</dt><dd>{{ subtitleArtifact.segments }}</dd></div>
        </dl>
        <div v-if="statItems.length" class="stat-grid">
          <div v-for="item in statItems" :key="item.label" class="stat">
            <strong>{{ item.value }}</strong>
            <span>{{ item.label }}</span>
          </div>
        </div>
        <div class="divider"></div>
        <div class="row tiny muted privacy-row">
          <Icon name="layers" :size="14" />
          {{ job.id }}
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.results {
  gap: 18px;
}

.results-heading {
  margin-bottom: 17px;
}

.results-heading h2 {
  font-size: 22px;
}

.side-kicker {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 6px;
}

.zip-size {
  font-size: 11.5px;
  font-weight: 500;
  opacity: 0.75;
}

.audio-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cue-loading {
  padding-left: 2px;
}

.alert.compact {
  padding: 10px 12px;
  font-size: 12.5px;
}

.text-section {
  margin-top: 22px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}

.text-section-head {
  align-items: flex-end;
  gap: 14px;
  margin-bottom: 13px;
}

.text-section-head p {
  margin: 4px 0 0;
}

.results-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
  gap: 18px;
  align-items: start;
}

.compact-heading {
  margin-bottom: 12px;
}

.compact-heading h2 {
  font-size: 17px;
}

.compact-heading p {
  font-size: 12.5px;
}

.download-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.download-item {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 11px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-soft);
  color: var(--text);
  text-decoration: none;
  transition: 0.15s ease;
}

.download-item:hover {
  border-color: var(--accent);
  background: var(--surface-2);
  text-decoration: none;
  transform: translateY(-1px);
}

.download-icon {
  width: 33px;
  height: 33px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: var(--accent-soft);
  color: var(--accent);
  flex: 0 0 auto;
}

.download-item strong {
  display: block;
  font-size: 13px;
}

.download-item small {
  display: block;
  color: var(--muted);
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.download-arrow {
  color: var(--muted);
  flex: 0 0 auto;
}

.download-item:hover .download-arrow {
  color: var(--accent);
}

.no-downloads {
  padding: 10px 2px;
}

.zip-button {
  margin-top: 12px;
}

.details-card {
  background:
    radial-gradient(circle at 100% 0, var(--accent-soft), transparent 40%),
    var(--surface);
}

.details {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0 0 15px;
}

.details > div {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  font-size: 12.5px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 7px;
}

.details > div:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.details dt {
  color: var(--muted);
  flex: 0 0 auto;
}

.details dd {
  margin: 0;
  min-width: 0;
  text-align: right;
  color: var(--text-soft);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}

.stat {
  padding: 9px 10px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--bg-soft);
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.stat strong {
  font-size: 15px;
  font-variant-numeric: tabular-nums;
}

.stat span {
  color: var(--muted);
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 650;
}

.privacy-row {
  gap: 7px;
  word-break: break-all;
}

.compact-empty {
  padding: 32px 16px;
  flex-direction: row;
  justify-content: flex-start;
  text-align: left;
}

.compact-empty h3 {
  font-size: 15px;
  margin-bottom: 2px;
}

@media (max-width: 960px) {
  .results-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 620px) {
  .results-heading {
    align-items: stretch;
  }
  .results-heading .btn {
    width: 100%;
  }
  .text-section-head {
    align-items: flex-start;
  }
  .tabs {
    width: 100%;
  }
  .tabs button {
    flex: 1;
    justify-content: center;
    padding: 8px;
  }
  .stat-grid {
    grid-template-columns: 1fr;
  }
}
</style>
