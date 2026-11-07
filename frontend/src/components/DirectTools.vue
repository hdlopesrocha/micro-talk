<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, handleApiError } from "../api/client";
import type {
  Catalog,
  Cue,
  DirectTTSResult,
  LanguageCode,
  OutputLanguage,
  SummaryLanguage,
  SummaryResult,
  TranslationResult,
  Voice,
} from "../api/types";
import { copyToClipboard } from "../utils/clipboard";
import { downloadText } from "../utils/download";
import { countWords, formatDuration, languageLabel } from "../utils/format";
import {
  missingTranslationPairs,
  translationRoute,
  translationRouteLabel,
} from "../utils/translation";
import AudioPlayer from "./AudioPlayer.vue";
import Icon from "./Icon.vue";

const props = defineProps<{
  catalog: Catalog | null;
  catalogLoading: boolean;
}>();

type Tool = "translate" | "summary" | "tts";
const tools: Array<{ id: Tool; label: string; icon: string; description: string }> = [
  {
    id: "translate",
    label: "Translate",
    icon: "translate",
    description: "Portuguese, English and French with the local OPUS-MT models.",
  },
  {
    id: "summary",
    label: "Summarize",
    icon: "sparkles",
    description: "Hierarchical local T5 summaries, even for long inputs.",
  },
  {
    id: "tts",
    label: "Narrate",
    icon: "volume",
    description: "Piper speech with a real sentence timeline and WAV or MP3 output.",
  },
];

const activeTool = ref<Tool>("translate");
const sourceText = ref("");
const notice = ref("");
const defaultsApplied = ref(false);
let noticeTimer: number | undefined;

const translateSource = ref<LanguageCode>("auto");
const translateTarget = ref<OutputLanguage>("en");
const translation = ref<TranslationResult | null>(null);
const translating = ref(false);
const translationError = ref("");

const summarySource = ref<LanguageCode>("auto");
const summaryLanguage = ref<SummaryLanguage>("en");
const summaryLength = ref<"short" | "medium" | "long">("medium");
const summary = ref<SummaryResult | null>(null);
const summarizing = ref(false);
const summaryError = ref("");

const ttsVoice = ref("");
const ttsSpeed = ref(1);
const ttsFormat = ref<"mp3" | "wav">("mp3");
const ttsNormalize = ref(false);
const ttsPreservePhrases = ref(true);
const ttsIgnoreLeadingNumbers = ref(false);
const ttsResult = ref<DirectTTSResult | null>(null);
const ttsNormalizeUsed = ref(false);
const ttsJobId = ref("");
const ttsCues = ref<Cue[]>([]);
const ttsStarting = ref(false);
const ttsPolling = ref(false);
const ttsCancelling = ref(false);
const ttsError = ref("");
const ttsTimelineWarning = ref("");
let pollGeneration = 0;
let disposed = false;

const maxChars = computed(() => props.catalog?.limits?.max_text_chars || 500_000);
const wordCount = computed(() => countWords(sourceText.value));
const voices = computed<Voice[]>(() => props.catalog?.tts?.voices || []);
const availableVoices = computed(() => voices.value.filter((voice) => voice.available));
const selectedVoice = computed(() => voices.value.find((voice) => voice.id === ttsVoice.value) || null);
const voiceGroups = computed(() => {
  const groups = new Map<string, Voice[]>();
  for (const voice of voices.value) {
    const list = groups.get(voice.language_name) || [];
    list.push(voice);
    groups.set(voice.language_name, list);
  }
  return [...groups.entries()].map(([name, items]) => ({ name, voices: items }));
});
const translationModelsMissing = computed(() => {
  const status = props.catalog?.translation_status;
  if (!status) return false;
  if (status.unavailable) return true;
  const pairs = status.supported_pairs || {};
  return Object.values(pairs).length > 0 && !Object.values(pairs).some(Boolean);
});
const summaryModelsMissing = computed(() => {
  const status = props.catalog?.summary_status;
  if (!status) return false;
  if (status.unavailable) return true;
  const available = status.available || {};
  return Object.values(available).length > 0 && !Object.values(available).some(Boolean);
});
const translationPairs = computed(() => props.catalog?.translation_status?.supported_pairs || {});
const translationRequiredPairs = computed(() =>
  translateSource.value === "auto"
    ? []
    : translationRoute(translateSource.value, translateTarget.value),
);
const translationMissingPairs = computed(() =>
  missingTranslationPairs(translationRequiredPairs.value, translationPairs.value),
);
const translationBlocked = computed(
  () => translationModelsMissing.value || translationMissingPairs.value.length > 0,
);
const ttsModelsMissing = computed(
  () => !props.catalogLoading && voices.value.length > 0 && availableVoices.value.length === 0,
);
const ttsActive = computed(() =>
  ["queued", "processing"].includes(String(ttsResult.value?.status || "")),
);
const ttsCompleted = computed(() => ttsResult.value?.status === "completed");
const ttsProgress = computed(() => Number(ttsResult.value?.progress || 0));
const ttsOutputFormat = computed(() => String(ttsResult.value?.format || ttsFormat.value));
// A WAV output without normalization is the master WAV itself, so the extra
// "master" download would point at the same bytes.
const ttsHasSeparateWavMaster = computed(
  () => ttsOutputFormat.value === "mp3" || ttsNormalizeUsed.value,
);
const ttsAudioSrc = computed(() =>
  ttsCompleted.value && ttsJobId.value
    ? api.directAudioUrl(ttsJobId.value, false, true)
    : "",
);
const ttsBusy = computed(() => ttsStarting.value || ttsActive.value);

watch(
  () => props.catalog,
  (catalog) => {
    if (!catalog || defaultsApplied.value) return;
    defaultsApplied.value = true;
    translateTarget.value = catalog.defaults?.translation_target || "en";
    const preferredSummary = catalog.defaults?.translation_target;
    summaryLanguage.value =
      preferredSummary === "pt-PT" || preferredSummary === "pt-BR" ? preferredSummary : "en";
    summaryLength.value = catalog.defaults?.summary_length || "medium";
    ttsSpeed.value = catalog.defaults?.tts_speed || 1;
    ttsFormat.value = catalog.defaults?.tts_format || "mp3";
    const preferred = catalog.defaults?.tts_voice || catalog.tts?.default_voice || "";
    const candidate =
      availableVoices.value.find((voice) => voice.id === preferred) || availableVoices.value[0];
    if (candidate) ttsVoice.value = candidate.id;
    else if (voices.value[0]) ttsVoice.value = voices.value[0].id;
  },
  { immediate: true, deep: true },
);

onBeforeUnmount(() => {
  disposed = true;
  pollGeneration += 1;
  if (noticeTimer) window.clearTimeout(noticeTimer);
  if (ttsJobId.value && ["queued", "processing"].includes(String(ttsResult.value?.status || ""))) {
    void api.directTtsCancel(ttsJobId.value).catch(() => undefined);
  }
});

function setNotice(message: string) {
  notice.value = message;
  if (noticeTimer) window.clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => {
    notice.value = "";
  }, 3600);
}

async function copyValue(value: string, label: string) {
  const copied = await copyToClipboard(value);
  setNotice(copied ? `${label} copied to the clipboard.` : "Could not access the clipboard.");
}

function useAsSource(value: string) {
  sourceText.value = value;
  setNotice("Result moved into the source text.");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function runTranslation() {
  if (translating.value || translationBlocked.value) return;
  if (!sourceText.value.trim()) {
    translationError.value = "Paste some text to translate.";
    return;
  }
  translating.value = true;
  translationError.value = "";
  translation.value = null;
  try {
    translation.value = await api.directTranslate({
      text: sourceText.value,
      source_language: translateSource.value,
      target_language: translateTarget.value,
    });
  } catch (error) {
    translationError.value = handleApiError(error);
  } finally {
    translating.value = false;
  }
}

async function runSummary() {
  if (summarizing.value || summaryModelsMissing.value) return;
  if (!sourceText.value.trim()) {
    summaryError.value = "Paste some text to summarize.";
    return;
  }
  summarizing.value = true;
  summaryError.value = "";
  summary.value = null;
  try {
    summary.value = await api.directSummarize({
      text: sourceText.value,
      language: summaryLanguage.value,
      source_language: summarySource.value,
      length: summaryLength.value,
    });
  } catch (error) {
    summaryError.value = handleApiError(error);
  } finally {
    summarizing.value = false;
  }
}

async function runTts() {
  if (ttsBusy.value) return;
  if (!sourceText.value.trim()) {
    ttsError.value = "Paste some text to narrate.";
    return;
  }
  if (!selectedVoice.value || !selectedVoice.value.available) {
    ttsError.value = "Choose an installed Piper voice first.";
    return;
  }
  if (ttsJobId.value && ["queued", "processing"].includes(String(ttsResult.value?.status || ""))) {
    try {
      await api.directTtsCancel(ttsJobId.value);
    } catch {
      // Starting a new job supersedes the old one either way.
    }
  }
  pollGeneration += 1;
  const generation = pollGeneration;
  ttsStarting.value = true;
  ttsPolling.value = false;
  ttsError.value = "";
  ttsTimelineWarning.value = "";
  ttsCues.value = [];
  ttsResult.value = null;
  ttsJobId.value = "";
  ttsNormalizeUsed.value = ttsNormalize.value;
  try {
    const created = await api.directTts({
      text: sourceText.value,
      voice: ttsVoice.value,
      language: selectedVoice.value.language,
      speed: ttsSpeed.value,
      format: ttsFormat.value,
      normalize_audio: ttsNormalize.value,
      preserve_phrases: ttsPreservePhrases.value,
      ignore_leading_numbers: ttsIgnoreLeadingNumbers.value,
    });
    if (generation !== pollGeneration) return;
    ttsResult.value = created;
    ttsJobId.value = created.job_id;
    if (created.status === "completed") {
      await finishTts(created, generation);
    } else if (created.status === "failed" || created.status === "cancelled") {
      ttsError.value = created.error?.message || "Speech synthesis did not complete.";
    } else {
      await pollTts(created.job_id, generation);
    }
  } catch (error) {
    if (generation === pollGeneration) ttsError.value = handleApiError(error);
  } finally {
    if (generation === pollGeneration) ttsStarting.value = false;
  }
}

async function pollTts(jobId: string, generation: number) {
  ttsPolling.value = true;
  try {
    while (!disposed && generation === pollGeneration) {
      await delay(1_100);
      if (disposed || generation !== pollGeneration) break;
      const state = await api.directTtsStatus(jobId);
      if (generation !== pollGeneration) break;
      ttsResult.value = state;
      if (state.status === "completed") {
        await finishTts(state, generation);
        break;
      }
      if (state.status === "failed" || state.status === "cancelled") {
        ttsError.value =
          state.error?.message ||
          (state.status === "cancelled" ? "Narration was cancelled." : "Speech synthesis failed.");
        break;
      }
    }
  } catch (error) {
    if (generation === pollGeneration) ttsError.value = handleApiError(error);
  } finally {
    if (generation === pollGeneration) ttsPolling.value = false;
  }
}

async function finishTts(state: DirectTTSResult, generation: number) {
  if (generation !== pollGeneration) return;
  try {
    const timeline = await api.directTtsTimeline(state.job_id);
    if (generation !== pollGeneration) return;
    ttsCues.value = timeline.segments || [];
  } catch (error) {
    ttsTimelineWarning.value = `Audio is ready, but the subtitle timeline could not be loaded. ${handleApiError(error)}`;
  }
}

async function cancelTts() {
  if (!ttsJobId.value || !ttsActive.value || ttsCancelling.value) return;
  ttsCancelling.value = true;
  pollGeneration += 1;
  ttsPolling.value = false;
  try {
    const state = await api.directTtsCancel(ttsJobId.value);
    ttsResult.value = state;
    ttsError.value = "Narration was cancelled.";
  } catch (error) {
    ttsError.value = handleApiError(error);
  } finally {
    ttsCancelling.value = false;
  }
}

function downloadTranslation() {
  if (!translation.value) return;
  downloadText("microtalk-translation.txt", translation.value.translated_text);
}

function downloadSummary() {
  if (!summary.value) return;
  downloadText("microtalk-summary.txt", summary.value.summary);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
</script>

<template>
  <div class="direct-tools">
    <section class="card panel tools-hero rise">
      <div class="grow">
        <span class="kicker"><Icon name="tools" :size="13" /> Quick tools</span>
        <h1>One-off local tools</h1>
        <p>
          Translate, summarize or narrate a piece of text without creating a full workflow. The
          same local models and CPU-only services are used.
        </p>
      </div>
      <div class="hero-feature">
        <Icon name="layers" :size="18" />
        <div>
          <strong>No cloud calls</strong>
          <span>Text stays inside Docker</span>
        </div>
      </div>
    </section>

    <section class="card panel tool-panel">
      <div class="tabs tool-tabs" role="tablist" aria-label="Quick tool">
        <button
          v-for="tool in tools"
          :key="tool.id"
          type="button"
          role="tab"
          :aria-selected="activeTool === tool.id"
          :class="{ active: activeTool === tool.id }"
          @click="activeTool = tool.id"
        >
          <Icon :name="tool.icon" :size="15" />
          {{ tool.label }}
        </button>
      </div>
      <p class="tool-description tiny muted">{{ tools.find((tool) => tool.id === activeTool)?.description }}</p>

      <div v-if="notice" class="alert success compact" role="status">
        <Icon name="check" :size="16" /><span>{{ notice }}</span>
      </div>

      <div class="field source-field">
        <div class="row between wrap">
          <label for="tool-text">Text</label>
          <span class="tiny muted">{{ wordCount.toLocaleString() }} words · {{ sourceText.length.toLocaleString() }} / {{ maxChars.toLocaleString() }} characters</span>
        </div>
        <textarea
          id="tool-text"
          v-model="sourceText"
          class="textarea large tools-text"
          :maxlength="maxChars"
          placeholder="Paste Portuguese, English or French text here…"
        ></textarea>
      </div>

      <!-- Translation -->
      <div v-if="activeTool === 'translate'" class="tool-body">
        <div class="settings-grid">
          <div class="field">
            <label for="quick-source">Source language</label>
            <select id="quick-source" v-model="translateSource" class="select">
              <option value="auto">Detect automatically</option>
              <option value="pt">Portuguese</option>
              <option value="pt-PT">Portuguese (Portugal)</option>
              <option value="pt-BR">Portuguese (Brazil)</option>
              <option value="en">English</option>
              <option value="fr">French</option>
            </select>
          </div>
          <div class="field">
            <label for="quick-target">Translate into</label>
            <select id="quick-target" v-model="translateTarget" class="select">
              <option value="en">English</option>
              <option value="pt-PT">Portuguese (Portugal)</option>
              <option value="pt-BR">Portuguese (Brazil)</option>
              <option value="fr">French</option>
            </select>
          </div>
          <div class="run-cell">
            <button class="btn primary" type="button" :disabled="translating || !sourceText.trim() || translationBlocked" @click="runTranslation">
              <span v-if="translating" class="spinner"></span>
              <Icon v-else name="translate" :size="17" />
              {{ translating ? "Translating…" : "Translate" }}
            </button>
          </div>
        </div>

        <div v-if="translationModelsMissing" class="alert warn">
          <Icon name="alert" :size="17" />
          <span>Translation models are not installed. Run <code>docker compose run --rm translation-service download-models</code>.</span>
        </div>
        <div v-else-if="translationMissingPairs.length" class="alert warn">
          <Icon name="alert" :size="17" />
          <span>
            The {{ translationMissingPairs.map((pair) => pair.toUpperCase()).join(", ") }} model(s)
            required for {{ translationRouteLabel(translationRequiredPairs) }} are missing. Run
            <code>docker compose run --rm translation-service download-models</code>.
          </span>
        </div>
        <div v-if="translationError" class="alert error" role="alert">
          <Icon name="alert" :size="17" /><span>{{ translationError }}</span>
        </div>

        <div v-if="translation" class="result-card">
          <div class="result-head">
            <div>
              <span class="badge accent"><Icon name="translate" :size="12" /> Translation</span>
              <div class="tiny muted result-sub">
                {{ translation.source_language_name }} → {{ translation.target_language_name }}
                <template v-if="translation.detected_source_language"> · language detected</template>
                · {{ translation.duration_seconds.toFixed(2) }}s
              </div>
            </div>
            <div class="row wrap result-actions">
              <button class="btn ghost small" type="button" @click="copyValue(translation.translated_text, 'Translation')">
                <Icon name="copy" :size="14" /> Copy
              </button>
              <button class="btn ghost small" type="button" @click="downloadTranslation">
                <Icon name="download" :size="14" /> TXT
              </button>
              <button class="btn ghost small" type="button" @click="useAsSource(translation.translated_text)">
                <Icon name="arrow-right" :size="14" /> Use as source
              </button>
            </div>
          </div>
          <textarea v-model="translation.translated_text" class="textarea result-textarea" aria-label="Editable translation"></textarea>
          <div class="result-meta tiny muted">
            <span>{{ translation.model }}</span>
            <span>{{ translation.translated_text.length.toLocaleString() }} characters</span>
          </div>
        </div>
      </div>

      <!-- Summary -->
      <div v-else-if="activeTool === 'summary'" class="tool-body">
        <div class="settings-grid summary-settings">
          <div class="field">
            <label for="summary-source">Source language</label>
            <select id="summary-source" v-model="summarySource" class="select">
              <option value="auto">Detect automatically</option>
              <option value="pt">Portuguese</option>
              <option value="pt-PT">Portuguese (Portugal)</option>
              <option value="pt-BR">Portuguese (Brazil)</option>
              <option value="en">English</option>
            </select>
          </div>
          <div class="field">
            <label for="summary-language">Summary language</label>
            <select id="summary-language" v-model="summaryLanguage" class="select">
              <option value="en">English</option>
              <option value="pt-PT">Portuguese (Portugal)</option>
              <option value="pt-BR">Portuguese (Brazil)</option>
            </select>
          </div>
          <div class="field">
            <span class="field-label">Length</span>
            <div class="segmented">
              <button
                v-for="length in ['short', 'medium', 'long'] as const"
                :key="length"
                type="button"
                :class="{ active: summaryLength === length }"
                @click="summaryLength = length"
              >
                {{ length[0].toUpperCase() + length.slice(1) }}
              </button>
            </div>
          </div>
          <div class="run-cell">
            <button class="btn primary" type="button" :disabled="summarizing || !sourceText.trim() || summaryModelsMissing" @click="runSummary">
              <span v-if="summarizing" class="spinner"></span>
              <Icon v-else name="sparkles" :size="17" />
              {{ summarizing ? "Summarizing…" : "Summarize" }}
            </button>
          </div>
        </div>

        <div v-if="summaryModelsMissing" class="alert warn">
          <Icon name="alert" :size="17" />
          <span>Summary models are not installed. Run <code>docker compose run --rm summary-service download-models</code>.</span>
        </div>
        <div v-if="summaryError" class="alert error" role="alert">
          <Icon name="alert" :size="17" /><span>{{ summaryError }}</span>
        </div>

        <div v-if="summary" class="result-card">
          <div class="result-head">
            <div>
              <span class="badge teal"><Icon name="sparkles" :size="12" /> Summary</span>
              <div class="tiny muted result-sub">
                {{ summary.input_words.toLocaleString() }} → {{ summary.output_words.toLocaleString() }} words
                · {{ summary.chunk_count }} chunk(s) · {{ summary.hierarchy_levels }} level(s)
                · {{ summary.duration_seconds.toFixed(2) }}s
              </div>
            </div>
            <div class="row wrap result-actions">
              <button class="btn ghost small" type="button" @click="copyValue(summary.summary, 'Summary')">
                <Icon name="copy" :size="14" /> Copy
              </button>
              <button class="btn ghost small" type="button" @click="downloadSummary">
                <Icon name="download" :size="14" /> TXT
              </button>
              <button class="btn ghost small" type="button" @click="useAsSource(summary.summary)">
                <Icon name="arrow-right" :size="14" /> Use as source
              </button>
            </div>
          </div>
          <textarea v-model="summary.summary" class="textarea result-textarea" aria-label="Editable summary"></textarea>
          <div v-if="summary.warnings.length" class="alert warn compact">
            <Icon name="info" :size="16" />
            <span>{{ summary.warnings.join(" ") }}</span>
          </div>
          <div class="result-meta tiny muted">
            <span>{{ summary.model }}</span>
            <span v-if="summary.translated">translated locally into {{ languageLabel(summary.language) }}</span>
          </div>
        </div>
      </div>

      <!-- TTS -->
      <div v-else class="tool-body">
        <div class="settings-grid tts-settings">
          <div class="field">
            <label for="quick-voice">Voice</label>
            <select id="quick-voice" v-model="ttsVoice" class="select">
              <optgroup v-for="group in voiceGroups" :key="group.name" :label="group.name">
                <option v-for="voice in group.voices" :key="voice.id" :value="voice.id" :disabled="!voice.available">
                  {{ voice.name }}<template v-if="voice.gender === 'female' || voice.gender === 'male'"> · {{ voice.gender }}</template>
                  · {{ voice.quality }}{{ voice.available ? "" : " (not installed)" }}
                </option>
              </optgroup>
            </select>
            <span v-if="selectedVoice" class="field-hint">{{ selectedVoice.description }}</span>
          </div>
          <div class="field">
            <label for="quick-speed">Speed <span class="mono soft">{{ ttsSpeed.toFixed(2) }}×</span></label>
            <input id="quick-speed" v-model.number="ttsSpeed" class="range" type="range" min="0.5" max="2" step="0.05" />
          </div>
          <div class="field">
            <span class="field-label">Format</span>
            <div class="segmented">
              <button type="button" :class="{ active: ttsFormat === 'mp3' }" @click="ttsFormat = 'mp3'">MP3</button>
              <button type="button" :class="{ active: ttsFormat === 'wav' }" @click="ttsFormat = 'wav'">WAV</button>
            </div>
          </div>
          <label class="check tts-normalize">
            <input v-model="ttsNormalize" type="checkbox" />
            <span class="check-text">
              Normalize loudness
              <small>FFmpeg dynaudnorm</small>
            </span>
          </label>
          <div class="run-cell">
            <button class="btn primary" type="button" :disabled="ttsBusy || !sourceText.trim() || ttsModelsMissing" @click="runTts">
              <span v-if="ttsStarting" class="spinner"></span>
              <Icon v-else name="volume" :size="17" />
              {{ ttsBusy ? "Narrating…" : "Narrate" }}
            </button>
          </div>
        </div>

        <div class="tts-options">
          <label class="check tts-normalize">
            <input v-model="ttsPreservePhrases" type="checkbox" />
            <span class="check-text">
              Keep sentences whole
              <small>Don't split long sentences at commas.</small>
            </span>
          </label>
          <label class="check tts-normalize">
            <input v-model="ttsIgnoreLeadingNumbers" type="checkbox" />
            <span class="check-text">
              Ignore leading numbers
              <small>Drop Bible-style verse numbers like “1 No princípio…”.</small>
            </span>
          </label>
        </div>

        <div v-if="ttsModelsMissing" class="alert warn">
          <Icon name="alert" :size="17" />
          <span>No Piper voices are installed. Run <code>docker compose run --rm tts-service download-models</code>.</span>
        </div>

        <div v-if="ttsActive || ttsStarting" class="tts-progress card panel-tight">
          <div class="row between wrap">
            <div class="row">
              <span class="badge accent"><span class="status-dot pulse"></span> {{ ttsResult?.status || "starting" }}</span>
              <span class="tiny muted">
                {{ ttsResult?.message || "Sending text to Piper…" }}
              </span>
            </div>
            <button class="btn danger small" type="button" :disabled="ttsCancelling || ttsStarting" @click="cancelTts">
              <span v-if="ttsCancelling" class="spinner"></span>
              <Icon v-else name="x" :size="14" />
              {{ ttsCancelling ? "Cancelling…" : "Cancel" }}
            </button>
          </div>
          <div class="progress">
            <span :style="{ width: `${Math.max(3, ttsProgress)}%` }"></span>
          </div>
          <div class="tiny muted row between">
            <span>{{ ttsProgress }}%</span>
            <span v-if="ttsResult?.sentence_count">
              {{ ttsResult.completed_sentences || 0 }} / {{ ttsResult.sentence_count }} sentences
            </span>
          </div>
        </div>

        <div v-if="ttsError" class="alert" :class="ttsResult?.status === 'cancelled' ? 'warn' : 'error'" role="alert">
          <Icon name="alert" :size="17" /><span>{{ ttsError }}</span>
        </div>

        <div v-if="ttsCompleted && ttsJobId" class="tts-result">
          <AudioPlayer
            :src="ttsAudioSrc"
            :cues="ttsCues"
            title="Direct narration"
            :format="ttsOutputFormat"
            :duration-hint="ttsResult?.duration_seconds || null"
          />
          <div v-if="ttsTimelineWarning" class="alert warn compact">
            <Icon name="alert" :size="16" /><span>{{ ttsTimelineWarning }}</span>
          </div>
          <div class="row wrap tts-downloads">
            <a class="btn ghost" :href="api.directAudioUrl(ttsJobId, false, false)" download>
              <Icon name="download" :size="16" /> Download {{ ttsOutputFormat.toUpperCase() }}
            </a>
            <a v-if="ttsHasSeparateWavMaster" class="btn ghost" :href="api.directAudioUrl(ttsJobId, true, false)" download>
              <Icon name="download" :size="16" /> Download WAV master
            </a>
            <span v-if="ttsResult?.duration_seconds" class="tiny muted">
              {{ formatDuration(ttsResult.duration_seconds) }} · {{ ttsCues.length }} cues
            </span>
          </div>
        </div>
      </div>

      <div v-if="catalogLoading" class="tiny muted tools-loading">
        <span class="spinner"></span> Loading local model and voice catalog…
      </div>
    </section>
  </div>
</template>

<style scoped>
.direct-tools {
  display: flex;
  flex-direction: column;
  gap: 17px;
}

.tools-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
  background:
    radial-gradient(circle at 100% 0, var(--amber-soft), transparent 44%),
    var(--surface);
}

.kicker {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--amber);
  font-size: 10.5px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 9px;
}

.tools-hero h1 {
  font-size: clamp(27px, 3.7vw, 40px);
  letter-spacing: -0.04em;
  margin-bottom: 7px;
}

.tools-hero p {
  max-width: 660px;
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}

.hero-feature {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid color-mix(in srgb, var(--teal) 26%, transparent);
  border-radius: 12px;
  background: var(--teal-soft);
  color: var(--teal);
}

.hero-feature div {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.hero-feature strong {
  font-size: 12.5px;
}

.hero-feature span {
  font-size: 11.5px;
  opacity: 0.8;
}

.tool-panel {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.tool-tabs {
  width: fit-content;
}

.tool-description {
  margin: -5px 0 0;
}

.alert.compact {
  padding: 10px 12px;
  font-size: 12.5px;
}

.source-field {
  gap: 8px;
}

.tools-text {
  min-height: 230px;
}

.tool-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  border-top: 1px solid var(--border);
  padding-top: 16px;
}

.settings-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
  gap: 14px;
  align-items: end;
}

.summary-settings {
  grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
}

.tts-settings {
  grid-template-columns: 1.5fr 1fr 0.8fr auto auto;
}

.tts-normalize {
  padding-bottom: 10px;
}

.tts-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  border-top: 1px solid var(--border);
  padding-top: 14px;
}

.run-cell {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 130px;
}

.run-cell .btn {
  min-height: 42px;
  width: 100%;
}

.result-card {
  display: flex;
  flex-direction: column;
  gap: 11px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
}

.result-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.result-sub {
  margin-top: 4px;
}

.result-actions {
  gap: 6px;
}

.result-textarea {
  min-height: 240px;
  font-size: 14px;
  line-height: 1.65;
}

.result-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  font-family: var(--font-mono);
  font-size: 10.5px;
  word-break: break-all;
}

.tts-progress {
  display: flex;
  flex-direction: column;
  gap: 9px;
  background: var(--bg-soft);
}

.tts-result {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tts-downloads {
  align-items: center;
  gap: 8px;
}

.tools-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 4px;
}

code {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--surface-3);
  color: var(--text);
  font-size: 11.5px;
  font-family: var(--font-mono);
  word-break: break-all;
}

@media (max-width: 1000px) {
  .settings-grid,
  .summary-settings,
  .tts-settings {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .run-cell {
    grid-column: 1 / -1;
  }
}

@media (max-width: 620px) {
  .tools-hero {
    align-items: flex-start;
  }
  .hero-feature {
    width: 100%;
  }
  .tool-tabs {
    width: 100%;
  }
  .tool-tabs button {
    flex: 1;
    justify-content: center;
    padding: 8px 6px;
    font-size: 12.5px;
  }
  .settings-grid,
  .summary-settings,
  .tts-settings,
  .tts-options {
    grid-template-columns: 1fr;
  }
  .run-cell {
    grid-column: auto;
  }
  .result-head {
    flex-direction: column;
  }
  .result-actions {
    width: 100%;
  }
  .result-actions .btn {
    flex: 1;
  }
}
</style>
