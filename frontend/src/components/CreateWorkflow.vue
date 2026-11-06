<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { api, handleApiError } from "../api/client";
import type {
  Catalog,
  CreateJobRequest,
  JobState,
  LanguageCode,
  OutputLanguage,
  PDFDocument,
  Voice,
} from "../api/types";
import { formatBytes, languageLabel } from "../utils/format";
import {
  resolvePageSelection,
  validatePageSelection,
  type PageMode,
  type PageSelectionValue,
} from "../utils/pages";
import DocumentPages from "./DocumentPages.vue";
import Icon from "./Icon.vue";

const props = defineProps<{
  catalog: Catalog | null;
  catalogLoading: boolean;
}>();

const emit = defineEmits<{
  created: [job: JobState];
}>();

const fileInput = ref<HTMLInputElement | null>(null);
const sourceMode = ref<"pdf" | "text">("pdf");
const document = ref<PDFDocument | null>(null);
const uploading = ref(false);
const uploadProgress = ref(0);
const dragActive = ref(false);
const uploadError = ref("");
const text = ref("");
const sourceLanguage = ref<LanguageCode>("auto");
const ocrEnabled = ref(false);
const pageMode = ref<PageMode>("all");
const rangeStart = ref(1);
const rangeEnd = ref(1);
const customSelected = ref<number[]>([]);
const title = ref("");

const translationEnabled = ref(true);
const targetLanguage = ref<OutputLanguage>("en");
const summaryEnabled = ref(false);
const summaryLength = ref<"short" | "medium" | "long">("medium");
const summaryLanguage = ref<"auto" | OutputLanguage>("auto");
const ttsEnabled = ref(true);
const ttsVoice = ref("");
const ttsSpeed = ref(1);
const ttsFormat = ref<"mp3" | "wav">("mp3");
const ttsNormalize = ref(false);
const ttsTextSource = ref<"content" | "summary">("content");
const voiceTouched = ref(false);
const defaultsApplied = ref(false);
const submitting = ref(false);
const submitError = ref("");

const voices = computed<Voice[]>(() => props.catalog?.tts?.voices || []);
const availableVoices = computed(() => voices.value.filter((voice) => voice.available));
const selectedVoice = computed(() => voices.value.find((voice) => voice.id === ttsVoice.value) || null);
const pageCount = computed(() => document.value?.page_count || 0);
const maxUploadMb = computed(() => props.catalog?.limits?.max_upload_mb || 100);
const maxTextChars = computed(() => props.catalog?.limits?.max_text_chars || 500000);

const translationPairs = computed(() => props.catalog?.translation_status?.supported_pairs || {});
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
const ttsModelsMissing = computed(
  () => !props.catalogLoading && voices.value.length > 0 && availableVoices.value.length === 0,
);

const pageSelection = computed<PageSelectionValue>(() => ({
  mode: pageMode.value,
  start: rangeStart.value,
  end: rangeEnd.value,
  selected: customSelected.value,
}));
const resolvedPages = computed(() =>
  pageCount.value ? resolvePageSelection(pageSelection.value, pageCount.value) : [],
);
const pageError = computed(() =>
  pageCount.value ? validatePageSelection(pageSelection.value, pageCount.value) : null,
);

watch(
  () => document.value?.id,
  (id) => {
    if (!id || !document.value) return;
    pageMode.value = "all";
    rangeStart.value = 1;
    rangeEnd.value = document.value.page_count;
    customSelected.value = [];
    ocrEnabled.value = document.value.likely_scanned;
  },
);

watch(pageMode, (mode) => {
  if (!document.value) return;
  if (mode === "range") {
    if (rangeStart.value > document.value.page_count || rangeEnd.value > document.value.page_count) {
      rangeStart.value = 1;
      rangeEnd.value = document.value.page_count;
    }
  }
  if (mode === "custom" && customSelected.value.length === 0) {
    customSelected.value = resolvePageSelection(
      {
        mode: "all",
        start: 1,
        end: document.value.page_count,
        selected: [],
      },
      document.value.page_count,
    );
  }
});

watch(summaryEnabled, (enabled) => {
  if (!enabled && ttsTextSource.value === "summary") ttsTextSource.value = "content";
});

function finalSpeechLanguage(): string | null {
  if (ttsTextSource.value === "summary" && summaryEnabled.value) {
    if (summaryLanguage.value !== "auto") return summaryLanguage.value;
    if (translationEnabled.value) return targetLanguage.value;
    return sourceLanguage.value === "auto" ? null : sourceLanguage.value;
  }
  if (translationEnabled.value) return targetLanguage.value;
  return sourceLanguage.value === "auto" ? null : sourceLanguage.value;
}

function languageBase(value: string | null | undefined): string | null {
  return (value || "").split("-")[0] || null;
}

function voiceMatchesLanguage(voice: Voice, language: string): boolean {
  const target = language.toLowerCase();
  const voiceLanguage = voice.language.toLowerCase();
  if (voiceLanguage.split("-")[0] !== target.split("-")[0]) return false;
  // An unqualified target such as "pt" or "en" accepts any regional voice.
  return target.includes("-") ? target === voiceLanguage : true;
}

const speechLanguage = computed(finalSpeechLanguage);
const speechBase = computed(() => languageBase(speechLanguage.value));
const recommendedVoice = computed(() => {
  const language = speechLanguage.value;
  if (!language) return null;
  const target = language.toLowerCase();
  return (
    availableVoices.value.find((voice) => voice.language.toLowerCase() === target) ||
    availableVoices.value.find((voice) => voiceMatchesLanguage(voice, language)) ||
    null
  );
});
const voiceMismatch = computed(() => {
  if (!ttsEnabled.value || !selectedVoice.value || !speechLanguage.value) return false;
  return !voiceMatchesLanguage(selectedVoice.value, speechLanguage.value);
});
const voiceWarning = computed(() => {
  if (!ttsEnabled.value || !selectedVoice.value) return "";
  if (!speechLanguage.value) {
    return "The document language will be detected when the workflow starts. Choose a source language so the selected voice can be matched, or enable translation.";
  }
  if (!voiceMismatch.value) return "";
  const selected = selectedVoice.value;
  const targetBase = speechBase.value;
  if (languageBase(selected.language) !== targetBase) {
    return `The selected voice speaks ${selected.language_name}, but the final text is ${languageLabel(
      speechLanguage.value,
    )}. Pick a matching voice.`;
  }
  return `The selected voice speaks ${selected.language_name}, but the final text is ${languageLabel(
    speechLanguage.value,
  )}. Pick the matching regional voice.`;
});

const translationRequiredPair = computed<string | null>(() => {
  if (!translationEnabled.value) return null;
  const source = sourceLanguage.value;
  if (source === "auto") return null;
  if (languageBase(source) === languageBase(targetLanguage.value)) return null;
  return languageBase(source) === "pt" ? "pt-en" : "en-pt";
});
const translationBlocked = computed(() => {
  if (!translationEnabled.value) return false;
  if (translationModelsMissing.value) return true;
  const pair = translationRequiredPair.value;
  return !!pair && translationPairs.value[pair] === false;
});
const translationAutoWarning = computed(() => {
  if (!translationEnabled.value || sourceLanguage.value !== "auto") return "";
  if (translationModelsMissing.value) return "";
  // Translation only needs a model when the detected language differs from the target.
  const pair = languageBase(targetLanguage.value) === "pt" ? "en-pt" : "pt-en";
  if (translationPairs.value[pair] !== false) return "";
  return `The ${pair.toUpperCase()} translation model is missing. Automatically detected ${languageLabel(
    languageBase(pair) || "",
  )} text will fail to translate; install the model or set the source language explicitly.`;
});
const translationSelectionWarning = computed(() => {
  const pair = translationRequiredPair.value;
  if (!pair || translationModelsMissing.value) return "";
  if (translationPairs.value[pair] !== false) return "";
  return `The ${pair.toUpperCase()} translation model required for this language pair is not installed. Run ${installCommand(
    "translation-service",
  )}.`;
});

const summaryModelLanguage = computed<string | null>(() => {
  if (!summaryEnabled.value) return null;
  if (summaryLanguage.value !== "auto") return languageBase(summaryLanguage.value);
  if (translationEnabled.value) return languageBase(targetLanguage.value);
  return languageBase(sourceLanguage.value);
});
const summaryBlocked = computed(() => {
  if (!summaryEnabled.value) return false;
  if (summaryModelsMissing.value) return true;
  const language = summaryModelLanguage.value;
  if (!language) return false;
  return (props.catalog?.summary_status?.available || {})[language] === false;
});
const summaryAutoWarning = computed(() => {
  if (!summaryEnabled.value || summaryLanguage.value !== "auto") return "";
  if (translationEnabled.value || summaryModelsMissing.value) return "";
  if (sourceLanguage.value !== "auto") return "";
  return "The summary language follows the detected document language. Choose a summary language or source language so the required local model can be checked before starting.";
});

function pickVoiceForLanguage(force = false) {
  if (!availableVoices.value.length) return;
  if (voiceTouched.value && !force) return;
  const recommended = recommendedVoice.value;
  const current = availableVoices.value.find((voice) => voice.id === ttsVoice.value);
  const currentMatches =
    current && speechLanguage.value && voiceMatchesLanguage(current, speechLanguage.value);
  if (recommended && (!current || !currentMatches)) {
    ttsVoice.value = recommended.id;
    voiceTouched.value = false;
  } else if (!current) {
    const preferred =
      availableVoices.value.find((voice) => voice.id === props.catalog?.defaults?.tts_voice) ||
      availableVoices.value.find((voice) => voice.id === props.catalog?.tts?.default_voice) ||
      availableVoices.value[0];
    ttsVoice.value = preferred.id;
  }
}

watch(
  () => props.catalog,
  () => {
    if (props.catalog && !defaultsApplied.value) {
      defaultsApplied.value = true;
      targetLanguage.value = props.catalog.defaults?.translation_target || "en";
      summaryLength.value = props.catalog.defaults?.summary_length || "medium";
      ttsSpeed.value = props.catalog.defaults?.tts_speed || 1;
      ttsFormat.value = props.catalog.defaults?.tts_format || "mp3";
    }
    if (!ttsVoice.value) {
      ttsVoice.value =
        availableVoices.value.find(
          (voice) =>
            voice.id === (props.catalog?.defaults?.tts_voice || props.catalog?.tts?.default_voice),
        )?.id ||
        availableVoices.value[0]?.id ||
        voices.value[0]?.id ||
        "";
    }
    pickVoiceForLanguage();
  },
  { immediate: true, deep: true },
);

watch([targetLanguage, sourceLanguage, translationEnabled, summaryEnabled, summaryLanguage, ttsTextSource], () => {
  pickVoiceForLanguage();
});

const availableSteps = computed(() => {
  const steps = [sourceMode.value === "pdf" ? "Extract" : "Prepare"];
  if (translationEnabled.value) steps.push("Translate");
  if (summaryEnabled.value) steps.push("Summarize");
  if (ttsEnabled.value) steps.push("Narrate", "Subtitles");
  steps.push("Bundle");
  return steps;
});

const canSubmit = computed(() => {
  if (submitting.value || props.catalogLoading) return false;
  if (sourceMode.value === "pdf") {
    if (!document.value || pageError.value) return false;
  } else if (!text.value.trim()) {
    return false;
  }
  if (translationBlocked.value || summaryBlocked.value) return false;
  if (ttsEnabled.value) {
    if (!selectedVoice.value || !selectedVoice.value.available) return false;
    if (!speechLanguage.value || voiceMismatch.value) return false;
  }
  return true;
});

async function uploadFile(file: File) {
  uploadError.value = "";
  if (!file.name.toLowerCase().endsWith(".pdf") && file.type !== "application/pdf") {
    uploadError.value = "Choose a PDF file.";
    return;
  }
  if (file.size > maxUploadMb.value * 1024 * 1024) {
    uploadError.value = `The file exceeds the ${maxUploadMb.value} MB limit.`;
    return;
  }
  uploading.value = true;
  uploadProgress.value = 0;
  try {
    const uploaded = await api.uploadDocument(file, (percent) => {
      uploadProgress.value = percent;
    });
    document.value = uploaded;
    title.value = title.value || uploaded.title || uploaded.filename.replace(/\.pdf$/i, "");
  } catch (error) {
    uploadError.value = handleApiError(error);
  } finally {
    uploading.value = false;
  }
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (file) uploadFile(file);
  target.value = "";
}

function onDrop(event: DragEvent) {
  dragActive.value = false;
  const file = event.dataTransfer?.files?.[0];
  if (file) uploadFile(file);
}

async function removeDocument() {
  const current = document.value;
  document.value = null;
  customSelected.value = [];
  if (current) {
    try {
      await api.deleteDocument(current.id);
    } catch {
      // The PDF service retention cleanup will eventually remove an orphan.
    }
  }
}

function togglePage(page: number) {
  if (pageMode.value !== "custom") pageMode.value = "custom";
  if (customSelected.value.includes(page)) {
    customSelected.value = customSelected.value.filter((item) => item !== page);
  } else {
    customSelected.value = [...customSelected.value, page].sort((a, b) => a - b);
  }
}

function switchToCustom() {
  pageMode.value = "custom";
}

function onSelectMany(pages: number[]) {
  pageMode.value = "custom";
  customSelected.value = pages;
}

async function startWorkflow() {
  submitError.value = "";
  if (!canSubmit.value) {
    submitError.value = pageError.value || "Complete the source and voice settings first.";
    return;
  }
  const input: CreateJobRequest["input"] = {
    type: sourceMode.value,
    language: sourceLanguage.value,
    ocr_enabled: sourceMode.value === "pdf" ? ocrEnabled.value : false,
  };
  if (sourceMode.value === "pdf" && document.value) {
    input.document_id = document.value.id;
    if (pageMode.value === "range") {
      input.start_page = Math.min(rangeStart.value, rangeEnd.value);
      input.end_page = Math.max(rangeStart.value, rangeEnd.value);
    } else if (pageMode.value === "custom") {
      input.selected_pages = [...customSelected.value].sort((a, b) => a - b);
    }
  } else {
    input.text = text.value;
  }
  const payload: CreateJobRequest = {
    title: title.value.trim(),
    input,
    translation: {
      enabled: translationEnabled.value,
      source_language: sourceLanguage.value,
      target_language: targetLanguage.value,
    },
    summary: {
      enabled: summaryEnabled.value,
      length: summaryLength.value,
      language: summaryLanguage.value === "auto" ? null : summaryLanguage.value,
    },
    tts: {
      enabled: ttsEnabled.value,
      voice: ttsVoice.value,
      speed: ttsSpeed.value,
      format: ttsFormat.value,
      normalize_audio: ttsNormalize.value,
      text_source: ttsTextSource.value,
    },
  };
  submitting.value = true;
  try {
    const job = await api.createJob(payload);
    emit("created", job);
    document.value = null;
    customSelected.value = [];
    title.value = "";
  } catch (error) {
    submitError.value = handleApiError(error);
  } finally {
    submitting.value = false;
  }
}

function installCommand(service: string): string {
  return `docker compose run --rm ${service} download-models`;
}
</script>

<template>
  <div class="create">
    <section class="hero rise">
      <div>
        <span class="eyebrow"><Icon name="sparkles" :size="14" /> Local AI document studio</span>
        <h1>Turn a PDF into something<br /><em>you can listen to.</em></h1>
        <p>
          Extract, translate, summarize and narrate Portuguese or English documents with local
          models. Nothing is sent to a cloud provider.
        </p>
      </div>
      <div class="hero-card">
        <div class="hero-stat">
          <strong>4</strong>
          <span>local engines</span>
        </div>
        <div class="hero-stat">
          <strong>2</strong>
          <span>languages</span>
        </div>
        <div class="hero-stat">
          <strong>0</strong>
          <span>external APIs</span>
        </div>
        <div class="hero-flow">
          <span>PDF</span><Icon name="arrow-right" :size="13" /><span>Text</span>
          <Icon name="arrow-right" :size="13" /><span>Speech</span>
          <Icon name="arrow-right" :size="13" /><span>SRT</span>
        </div>
      </div>
    </section>

    <div class="workflow-grid">
      <div class="main-column stack">
        <!-- 1. Source -->
        <section class="card panel">
          <div class="section-heading">
            <div>
              <span class="section-number">01 · Source</span>
              <h2>Choose your document</h2>
              <p>Upload a PDF with page-level control, or paste text directly.</p>
            </div>
            <div class="segmented" role="group" aria-label="Source type">
              <button
                type="button"
                :class="{ active: sourceMode === 'pdf' }"
                @click="sourceMode = 'pdf'"
              >
                <Icon name="file" :size="14" /> PDF
              </button>
              <button
                type="button"
                :class="{ active: sourceMode === 'text' }"
                @click="sourceMode = 'text'"
              >
                <Icon name="text" :size="14" /> Text
              </button>
            </div>
          </div>

          <template v-if="sourceMode === 'pdf'">
            <div v-if="!document" class="dropzone-wrap">
              <button
                class="dropzone"
                :class="{ active: dragActive, uploading }"
                type="button"
                :disabled="uploading"
                @click="fileInput?.click()"
                @dragenter.prevent="dragActive = true"
                @dragover.prevent="dragActive = true"
                @dragleave.prevent="dragActive = false"
                @drop.prevent="onDrop"
              >
                <span class="drop-icon">
                  <span v-if="uploading" class="spinner large"></span>
                  <Icon v-else name="upload" :size="27" />
                </span>
                <span class="drop-title">
                  {{ uploading ? "Uploading your PDF…" : "Drop a PDF here, or browse" }}
                </span>
                <span class="drop-sub">
                  Maximum {{ maxUploadMb }} MB · page ranges · scanned-page OCR
                </span>
                <span v-if="uploading" class="progress upload-progress">
                  <span :style="{ width: `${uploadProgress}%` }"></span>
                </span>
              </button>
              <input
                ref="fileInput"
                class="sr-only"
                type="file"
                accept="application/pdf,.pdf"
                @change="onFileChange"
              />
            </div>

            <div v-if="uploadError" class="alert error" role="alert">
              <Icon name="alert" :size="18" /><span>{{ uploadError }}</span>
            </div>

            <div v-if="document" class="document-card">
              <div class="document-icon"><Icon name="file" :size="22" /></div>
              <div class="grow">
                <strong class="truncate">{{ document.filename }}</strong>
                <div class="tiny muted">
                  {{ document.page_count }} pages · {{ formatBytes(document.size_bytes) }}
                  <span v-if="document.title"> · {{ document.title }}</span>
                </div>
              </div>
              <span v-if="document.likely_scanned" class="badge amber">
                <Icon name="scan" :size="13" /> Possibly scanned
              </span>
              <button class="btn ghost small" type="button" @click="removeDocument">
                <Icon name="x" :size="14" /> Remove
              </button>
            </div>

            <div v-if="document?.likely_scanned" class="alert warn scan-alert">
              <Icon name="alert" :size="18" />
              <span>
                A sample of pages has little extractable text, so this PDF may be scanned. Enable
                OCR below to recognize the page images.
              </span>
            </div>

            <template v-if="document">
              <div class="pages-head">
                <div>
                  <span class="field-label">Pages to process</span>
                  <p class="field-hint">
                    {{ resolvedPages.length }} of {{ document.page_count }} selected ·
                    first {{ Math.min(2000, pageCount) }} page limit
                  </p>
                </div>
                <div class="segmented" role="group" aria-label="Page selection mode">
                  <button type="button" :class="{ active: pageMode === 'all' }" @click="pageMode = 'all'">
                    All
                  </button>
                  <button type="button" :class="{ active: pageMode === 'range' }" @click="pageMode = 'range'">
                    Range
                  </button>
                  <button type="button" :class="{ active: pageMode === 'custom' }" @click="switchToCustom">
                    Custom
                  </button>
                </div>
              </div>

              <div v-if="pageMode === 'range'" class="range-row">
                <div class="field">
                  <label for="range-start">From page</label>
                  <input
                    id="range-start"
                    v-model.number="rangeStart"
                    class="input"
                    type="number"
                    min="1"
                    :max="pageCount"
                  />
                </div>
                <span class="range-dash">to</span>
                <div class="field">
                  <label for="range-end">To page</label>
                  <input
                    id="range-end"
                    v-model.number="rangeEnd"
                    class="input"
                    type="number"
                    min="1"
                    :max="pageCount"
                  />
                </div>
                <div class="range-total">
                  <span class="tiny muted">Selected</span>
                  <strong>{{ resolvedPages.length }} pages</strong>
                </div>
              </div>

              <div v-if="pageError" class="alert error">
                <Icon name="alert" :size="18" /><span>{{ pageError }}</span>
              </div>

              <DocumentPages
                :document="document"
                :selected="resolvedPages"
                :interactive="pageMode === 'custom'"
                @toggle="togglePage"
                @select-many="onSelectMany"
                @clear="customSelected = []"
              />

              <label class="check ocr-check">
                <input v-model="ocrEnabled" type="checkbox" />
                <span class="check-text">
                  Enable OCR for low-text pages
                  <small>
                    Tesseract recognizes Portuguese and English. Slower, but works on scanned
                    pages and images.
                  </small>
                </span>
              </label>
            </template>
          </template>

          <template v-else>
            <div class="field">
              <div class="row between">
                <label for="source-text">Paste your text</label>
                <span class="tiny muted">{{ text.length.toLocaleString() }} / {{ maxTextChars.toLocaleString() }} characters</span>
              </div>
              <textarea
                id="source-text"
                v-model="text"
                class="textarea large"
                :maxlength="maxTextChars"
                placeholder="Paste an article, report, transcript or any Portuguese or English text…"
              ></textarea>
              <span class="field-hint">
                Paragraph breaks are preserved through translation and used to build natural speech
                and subtitle cues.
              </span>
            </div>
          </template>
        </section>

        <!-- 2. Pipeline -->
        <section class="card panel">
          <div class="section-heading">
            <div>
              <span class="section-number">02 · Pipeline</span>
              <h2>Shape the result</h2>
              <p>Toggle only the stages you need. Each one runs locally on CPU.</p>
            </div>
            <span class="badge"><Icon name="layers" :size="13" /> {{ availableSteps.join(" → ") }}</span>
          </div>

          <div class="option-list">
            <!-- Translation -->
            <div class="option" :class="{ enabled: translationEnabled }">
              <div class="option-head">
                <div class="option-icon blue"><Icon name="translate" :size="19" /></div>
                <div class="grow">
                  <strong>Translate</strong>
                  <p>Neural Portuguese ↔ English with the OPUS-MT local models.</p>
                </div>
                <button
                  class="switch"
                  type="button"
                  role="switch"
                  :aria-checked="translationEnabled"
                  aria-label="Enable translation"
                  @click="translationEnabled = !translationEnabled"
                ></button>
              </div>
              <div v-if="translationEnabled" class="option-body">
                <div class="grid two">
                  <div class="field">
                    <label for="source-language">Document language</label>
                    <select id="source-language" v-model="sourceLanguage" class="select">
                      <option value="auto">Detect automatically</option>
                      <option value="pt">Portuguese</option>
                      <option value="pt-PT">Portuguese (Portugal)</option>
                      <option value="pt-BR">Portuguese (Brazil)</option>
                      <option value="en">English</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="target-language">Translate into</label>
                    <select id="target-language" v-model="targetLanguage" class="select">
                      <option value="en">English</option>
                      <option value="pt-PT">Portuguese (Portugal)</option>
                      <option value="pt-BR">Portuguese (Brazil)</option>
                    </select>
                  </div>
                </div>
                <div v-if="translationModelsMissing" class="alert warn">
                  <Icon name="alert" :size="17" />
                  <span>
                    Translation models are not installed yet. Run
                    <code>{{ installCommand("translation-service") }}</code> once, then restart
                    the workflow.
                  </span>
                </div>
                <div v-else class="tiny muted row">
                  <Icon name="check" :size="13" />
                  {{ translationPairs["pt-en"] ? "PT → EN ready" : "PT → EN missing" }} ·
                  {{ translationPairs["en-pt"] ? "EN → PT ready" : "EN → PT missing" }}
                </div>
                <div v-if="translationAutoWarning" class="alert warn">
                  <Icon name="alert" :size="17" /><span>{{ translationAutoWarning }}</span>
                </div>
                <div v-if="translationSelectionWarning" class="alert warn">
                  <Icon name="alert" :size="17" /><span>{{ translationSelectionWarning }}</span>
                </div>
              </div>
            </div>

            <!-- Summary -->
            <div class="option" :class="{ enabled: summaryEnabled }">
              <div class="option-head">
                <div class="option-icon teal"><Icon name="sparkles" :size="19" /></div>
                <div class="grow">
                  <strong>Summarize</strong>
                  <p>Hierarchical local T5 summaries for long documents.</p>
                </div>
                <button
                  class="switch"
                  type="button"
                  role="switch"
                  :aria-checked="summaryEnabled"
                  aria-label="Enable summarization"
                  @click="summaryEnabled = !summaryEnabled"
                ></button>
              </div>
              <div v-if="summaryEnabled" class="option-body">
                <div class="grid two">
                  <div class="field">
                    <span class="field-label">Length</span>
                    <div class="segmented" role="group" aria-label="Summary length">
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
                  <div class="field">
                    <label for="summary-language">Summary language</label>
                    <select id="summary-language" v-model="summaryLanguage" class="select">
                      <option value="auto">Same as final text</option>
                      <option value="en">English</option>
                      <option value="pt-PT">Portuguese (Portugal)</option>
                      <option value="pt-BR">Portuguese (Brazil)</option>
                    </select>
                  </div>
                </div>
                <div v-if="summaryModelsMissing" class="alert warn">
                  <Icon name="alert" :size="17" />
                  <span>
                    Summary models are not installed. Run
                    <code>{{ installCommand("summary-service") }}</code>.
                  </span>
                </div>
                <div v-else-if="summaryBlocked" class="alert warn">
                  <Icon name="alert" :size="17" />
                  <span>
                    The {{ languageLabel(summaryModelLanguage || "") }} summary model is not
                    installed. Run <code>{{ installCommand("summary-service") }}</code>.
                  </span>
                </div>
                <div v-if="summaryAutoWarning" class="alert warn">
                  <Icon name="alert" :size="17" /><span>{{ summaryAutoWarning }}</span>
                </div>
              </div>
            </div>

            <!-- TTS -->
            <div class="option" :class="{ enabled: ttsEnabled }">
              <div class="option-head">
                <div class="option-icon amber"><Icon name="volume" :size="19" /></div>
                <div class="grow">
                  <strong>Narrate with Piper</strong>
                  <p>Local neural voices, sentence-by-sentence with real durations.</p>
                </div>
                <button
                  class="switch"
                  type="button"
                  role="switch"
                  :aria-checked="ttsEnabled"
                  aria-label="Enable speech synthesis"
                  @click="ttsEnabled = !ttsEnabled"
                ></button>
              </div>
              <div v-if="ttsEnabled" class="option-body">
                <div class="grid two">
                  <div class="field">
                    <label for="tts-voice">Voice</label>
                    <select
                      id="tts-voice"
                      v-model="ttsVoice"
                      class="select"
                      @change="voiceTouched = true"
                    >
                      <optgroup
                        v-for="group in ['Portuguese (Portugal)', 'Portuguese (Brazil)', 'English (United States)']"
                        :key="group"
                      >
                        <template
                          v-for="voice in voices.filter((item) => item.language_name === group)"
                          :key="voice.id"
                        >
                          <option :value="voice.id" :disabled="!voice.available">
                            {{ voice.name }} · {{ voice.quality }}
                            {{ voice.available ? "" : " (not installed)" }}
                          </option>
                        </template>
                      </optgroup>
                    </select>
                    <span v-if="selectedVoice" class="field-hint">
                      {{ selectedVoice.description }}
                      <template v-if="!selectedVoice.available">
                        Run <code>{{ installCommand("tts-service") }}</code>.
                      </template>
                    </span>
                  </div>
                  <div class="field">
                    <label for="tts-speed">
                      Speed <span class="mono soft">{{ ttsSpeed.toFixed(2) }}×</span>
                    </label>
                    <input
                      id="tts-speed"
                      v-model.number="ttsSpeed"
                      class="range"
                      type="range"
                      min="0.5"
                      max="2"
                      step="0.05"
                    />
                    <div class="row between tiny muted">
                      <span>Slower</span><span>Natural</span><span>Faster</span>
                    </div>
                  </div>
                </div>

                <div v-if="!translationEnabled" class="field">
                  <label for="tts-source-language">Spoken text language</label>
                  <select id="tts-source-language" v-model="sourceLanguage" class="select">
                    <option value="auto">Detect automatically</option>
                    <option value="pt">Portuguese</option>
                    <option value="pt-PT">Portuguese (Portugal)</option>
                    <option value="pt-BR">Portuguese (Brazil)</option>
                    <option value="en">English</option>
                  </select>
                  <span class="field-hint">
                    Piper voices are language-specific. Choose the document language if automatic
                    detection is ambiguous.
                  </span>
                </div>

                <div v-if="ttsTextSource === 'summary' || summaryEnabled" class="field">
                  <span class="field-label">Narrate</span>
                  <div class="segmented" role="group" aria-label="Speech source">
                    <button
                      type="button"
                      :class="{ active: ttsTextSource === 'content' }"
                      @click="ttsTextSource = 'content'"
                    >
                      Full document
                    </button>
                    <button
                      type="button"
                      :class="{ active: ttsTextSource === 'summary' }"
                      :disabled="!summaryEnabled"
                      @click="ttsTextSource = 'summary'"
                    >
                      Summary only
                    </button>
                  </div>
                </div>

                <div class="grid two compact">
                  <div class="field">
                    <span class="field-label">Audio format</span>
                    <div class="segmented">
                      <button type="button" :class="{ active: ttsFormat === 'mp3' }" @click="ttsFormat = 'mp3'">
                        MP3
                      </button>
                      <button type="button" :class="{ active: ttsFormat === 'wav' }" @click="ttsFormat = 'wav'">
                        WAV
                      </button>
                    </div>
                  </div>
                  <label class="check normalize">
                    <input v-model="ttsNormalize" type="checkbox" />
                    <span class="check-text">
                      Normalize loudness
                      <small>FFmpeg dynaudnorm for consistent volume.</small>
                    </span>
                  </label>
                </div>

                <div v-if="voiceWarning" class="alert warn">
                  <Icon name="alert" :size="17" /><span>{{ voiceWarning }}</span>
                </div>
                <div v-if="ttsModelsMissing" class="alert warn">
                  <Icon name="alert" :size="17" />
                  <span>
                    No Piper voices are installed. Run
                    <code>{{ installCommand("tts-service") }}</code> once.
                  </span>
                </div>
                <p class="tiny muted pitch-note">
                  <Icon name="info" :size="13" /> Piper does not support pitch shifting, so the
                  workflow intentionally exposes speed, loudness and voice choice only.
                </p>
              </div>
            </div>
          </div>
        </section>

        <!-- 3. Run -->
        <section class="card panel run-panel">
          <div class="section-heading">
            <div>
              <span class="section-number">03 · Run</span>
              <h2>Start locally</h2>
              <p>Progress is real: extraction, translation batches, audio chunks and FFmpeg finalization.</p>
            </div>
            <span class="badge success"><span class="status-dot"></span> CPU only</span>
          </div>

          <div class="field title-field">
            <label for="job-title">Workflow title <span class="muted">(optional)</span></label>
            <input
              id="job-title"
              v-model="title"
              class="input"
              type="text"
              maxlength="200"
              placeholder="Quarterly report · English narration"
            />
          </div>

          <div class="review-grid">
            <div>
              <span class="review-label">Source</span>
              <strong>
                {{
                  sourceMode === "pdf"
                    ? document
                      ? `${document.filename} · ${resolvedPages.length} page(s)`
                      : "No PDF selected"
                    : text.trim()
                      ? `${text.trim().split(/\s+/).length.toLocaleString()} words`
                      : "No text pasted"
                }}
              </strong>
            </div>
            <div>
              <span class="review-label">Language</span>
              <strong>{{ languageLabel(sourceLanguage) }} → {{ translationEnabled ? languageLabel(targetLanguage) : "not translated" }}</strong>
            </div>
            <div>
              <span class="review-label">Outputs</span>
              <strong>{{ availableSteps.join(" · ") }}</strong>
            </div>
            <div>
              <span class="review-label">Voice</span>
              <strong>{{ ttsEnabled ? selectedVoice?.name || "Select a voice" : "Audio off" }}</strong>
            </div>
          </div>

          <div v-if="submitError" class="alert error" role="alert">
            <Icon name="alert" :size="18" /><span>{{ submitError }}</span>
          </div>

          <div class="run-actions">
            <p class="tiny muted run-note">
              Models load lazily to keep memory low. First run is slower; results are written to a
              persistent local volume.
            </p>
            <button class="btn primary large" type="button" :disabled="!canSubmit" @click="startWorkflow">
              <span v-if="submitting" class="spinner"></span>
              <Icon v-else name="sparkles" :size="18" />
              {{ submitting ? "Creating workflow…" : "Start local workflow" }}
            </button>
          </div>
        </section>
      </div>

      <aside class="side-column">
        <div class="card panel side-card">
          <span class="side-kicker">Local stack</span>
          <h3>What happens under the hood</h3>
          <ol class="pipeline-list">
            <li><span>1</span><div><strong>PyMuPDF</strong><small>Layout-aware extraction and Tesseract OCR</small></div></li>
            <li><span>2</span><div><strong>Marian / OPUS-MT</strong><small>Paragraph and page preserving translation</small></div></li>
            <li><span>3</span><div><strong>T5 summarizer</strong><small>Map-reduce summaries for long documents</small></div></li>
            <li><span>4</span><div><strong>Piper + FFmpeg</strong><small>Real sentence durations, final MP3/WAV and SRT</small></div></li>
          </ol>
          <div class="divider"></div>
          <div class="side-meta">
            <Icon name="clock" :size="15" />
            <span>Jobs continue in the background if you close this tab.</span>
          </div>
          <div class="side-meta">
            <Icon name="ear" :size="15" />
            <span>Subtitles are aligned to the actual generated audio, not fixed guesses.</span>
          </div>
        </div>

        <div v-if="catalog" class="card panel side-card status-card">
          <span class="side-kicker">Service status</span>
          <div class="service-row">
            <span>PDF &amp; OCR</span>
            <span class="badge" :class="(catalog.health?.pdf as any)?.status === 'ok' ? 'success' : 'danger'">
              {{ (catalog.health?.pdf as any)?.status || "unknown" }}
            </span>
          </div>
          <div class="service-row">
            <span>Translation</span>
            <span class="badge" :class="translationModelsMissing ? 'amber' : 'success'">
              {{ translationModelsMissing ? "models missing" : "ready" }}
            </span>
          </div>
          <div class="service-row">
            <span>Summarization</span>
            <span class="badge" :class="summaryModelsMissing ? 'amber' : 'success'">
              {{ summaryModelsMissing ? "models missing" : "ready" }}
            </span>
          </div>
          <div class="service-row">
            <span>Piper voices</span>
            <span class="badge" :class="ttsModelsMissing ? 'amber' : 'success'">
              {{ availableVoices.length }} available
            </span>
          </div>
          <div class="service-row">
            <span>Media &amp; FFmpeg</span>
            <span class="badge" :class="(catalog.health?.media as any)?.status === 'ok' ? 'success' : 'danger'">
              {{ (catalog.health?.media as any)?.status || "unknown" }}
            </span>
          </div>
        </div>

        <div class="card panel side-card privacy-card">
          <div class="privacy-icon"><Icon name="layers" :size="19" /></div>
          <h3>Private by architecture</h3>
          <p class="small soft">
            Only Nginx is exposed. The gateway, model services and Redis stay on the internal
            Docker network. No API keys, telemetry or cloud inference.
          </p>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(300px, 0.65fr);
  gap: 42px;
  align-items: end;
  margin-bottom: 34px;
  padding: 14px 4px 0;
}

.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 14px;
  padding: 5px 10px;
  border: 1px solid color-mix(in srgb, var(--teal) 28%, transparent);
  border-radius: 100px;
  background: var(--teal-soft);
  color: var(--teal);
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.hero h1 {
  font-size: clamp(34px, 5vw, 56px);
  line-height: 1.03;
  letter-spacing: -0.045em;
  margin-bottom: 16px;
}

.hero h1 em {
  font-style: normal;
  color: var(--accent);
  background: linear-gradient(100deg, var(--accent), var(--teal));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero p {
  max-width: 620px;
  color: var(--muted);
  font-size: 16px;
  margin: 0;
}

.hero-card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: linear-gradient(145deg, var(--surface-2), var(--surface));
  padding: 22px;
  box-shadow: var(--shadow-soft);
}

.hero-stat {
  display: flex;
  align-items: baseline;
  gap: 9px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}

.hero-stat strong {
  font-size: 28px;
  letter-spacing: -0.03em;
  color: var(--accent);
  min-width: 28px;
}

.hero-stat span {
  color: var(--muted);
  font-size: 13px;
}

.hero-flow {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 16px;
  font-size: 11.5px;
  color: var(--muted);
  font-weight: 600;
}

.hero-flow span {
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--surface-3);
}

.workflow-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 22px;
  align-items: start;
}

.main-column {
  min-width: 0;
}

.side-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
  position: sticky;
  top: calc(var(--header-height) + 18px);
}

.section-number {
  display: block;
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 6px;
}

.dropzone-wrap {
  border-radius: var(--radius-sm);
}

.dropzone {
  width: 100%;
  min-height: 190px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 26px;
  border: 1.5px dashed var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  color: var(--text-soft);
  transition: 0.18s ease;
}

.dropzone:hover,
.dropzone.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.drop-icon {
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  margin-bottom: 4px;
}

.drop-title {
  font-weight: 650;
  color: var(--text);
}

.drop-sub {
  font-size: 12.5px;
  color: var(--muted);
}

.upload-progress {
  max-width: 240px;
  margin-top: 10px;
}

.document-card {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
}

.document-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 11px;
  background: var(--accent-soft);
  color: var(--accent);
  flex: 0 0 auto;
}

.scan-alert {
  margin-top: 12px;
}

.pages-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  margin: 22px 0 12px;
}

.pages-head p {
  margin: 4px 0 0;
}

.range-row {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.range-row .field {
  width: 130px;
}

.range-dash {
  padding-bottom: 11px;
  color: var(--muted);
  font-size: 13px;
}

.range-total {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-bottom: 6px;
}

.range-total strong {
  font-size: 14px;
}

.ocr-check {
  margin-top: 14px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-soft);
}

.option-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.option {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--bg-soft);
  overflow: hidden;
  transition: border-color 0.18s ease;
}

.option.enabled {
  border-color: color-mix(in srgb, var(--accent) 42%, var(--border));
}

.option-head {
  display: flex;
  align-items: flex-start;
  gap: 13px;
  padding: 16px;
}

.option-head strong {
  display: block;
  margin-bottom: 2px;
}

.option-head p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.option-icon {
  width: 39px;
  height: 39px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 11px;
}

.option-icon.blue {
  background: var(--accent-soft);
  color: var(--accent);
}
.option-icon.teal {
  background: var(--teal-soft);
  color: var(--teal);
}
.option-icon.amber {
  background: var(--amber-soft);
  color: var(--amber);
}

.option-body {
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.option-body .grid.compact {
  gap: 16px;
  align-items: end;
}

.range {
  width: 100%;
  accent-color: var(--accent);
  height: 24px;
}

.normalize {
  padding-bottom: 2px;
}

.pitch-note {
  margin: 0;
  display: flex;
  gap: 6px;
  align-items: flex-start;
}

.title-field {
  margin-bottom: 18px;
}

.review-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-soft);
}

.review-grid > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.review-grid strong {
  font-size: 13.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-label {
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: var(--muted);
}

.run-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-top: 18px;
}

.run-note {
  margin: 0;
  max-width: 440px;
}

.side-kicker {
  display: block;
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 7px;
}

.side-card h3 {
  font-size: 17px;
  margin-bottom: 14px;
}

.pipeline-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 13px;
}

.pipeline-list li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.pipeline-list li > span {
  width: 23px;
  height: 23px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 7px;
  background: var(--surface-3);
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
}

.pipeline-list strong {
  display: block;
  font-size: 13px;
}

.pipeline-list small {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
}

.side-meta {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.45;
  padding: 6px 0;
}

.side-meta svg {
  color: var(--teal);
  margin-top: 2px;
}

.service-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.service-row:last-child {
  border-bottom: 0;
}

.privacy-card {
  background:
    radial-gradient(circle at 100% 0, var(--teal-soft), transparent 60%),
    var(--surface);
}

.privacy-icon {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: var(--teal-soft);
  color: var(--teal);
  margin-bottom: 12px;
}

.privacy-card h3 {
  margin-bottom: 7px;
}

.privacy-card p {
  margin: 0;
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

@media (max-width: 1050px) {
  .workflow-grid {
    grid-template-columns: 1fr;
  }
  .side-column {
    position: static;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .hero {
    grid-template-columns: 1fr;
    gap: 24px;
  }
  .hero-card {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
  }
  .hero-stat {
    border-bottom: 0;
    flex-direction: column;
    gap: 0;
    padding: 4px;
  }
  .hero-stat strong {
    font-size: 22px;
  }
  .hero-flow {
    grid-column: 1 / -1;
    margin-top: 4px;
  }
  .run-actions {
    flex-direction: column;
    align-items: stretch;
  }
  .run-note {
    max-width: none;
  }
  .review-grid {
    grid-template-columns: 1fr;
  }
  .side-column {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 520px) {
  .hero-card {
    grid-template-columns: 1fr 1fr 1fr;
    padding: 16px;
  }
  .option-head {
    padding: 13px;
  }
  .option-body {
    padding: 0 13px 13px;
  }
  .document-card {
    flex-wrap: wrap;
  }
  .document-card .btn {
    margin-left: 55px;
  }
  .pages-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
