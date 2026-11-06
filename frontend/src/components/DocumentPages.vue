<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { api } from "../api/client";
import type { PDFDocument } from "../api/types";
import { pageWindow } from "../utils/pages";
import Icon from "./Icon.vue";

const props = defineProps<{
  document: PDFDocument;
  selected: number[];
  interactive: boolean;
}>();

const emit = defineEmits<{
  toggle: [page: number];
  selectMany: [pages: number[]];
  clear: [];
}>();

const anchor = ref(1);
const loaded = reactive(new Set<number>());
const failed = reactive(new Set<number>());

const selectedSet = computed(() => new Set(props.selected));
const windowInfo = computed(() => pageWindow(props.document.page_count, anchor.value, 24));
const windowAllSelected = computed(() =>
  windowInfo.value.pages.every((page) => selectedSet.value.has(page)),
);

watch(
  () => props.document.id,
  () => {
    anchor.value = 1;
    loaded.clear();
    failed.clear();
  },
);

function move(delta: number) {
  const next = Math.min(
    props.document.page_count,
    Math.max(1, anchor.value + delta * 24),
  );
  anchor.value = next;
}

function toggle(page: number) {
  if (!props.interactive) return;
  emit("toggle", page);
}

function selectVisible() {
  if (!props.interactive) return;
  if (windowAllSelected.value) {
    emit(
      "selectMany",
      props.selected.filter((page) => !windowInfo.value.pages.includes(page)),
    );
  } else {
    emit("selectMany", [...new Set([...props.selected, ...windowInfo.value.pages])].sort((a, b) => a - b));
  }
}

function imageUrl(page: number): string {
  return api.documentPreviewUrl(props.document.id, page, 260);
}
</script>

<template>
  <div class="pages">
    <div class="pages-toolbar">
      <div class="row small">
        <Icon name="file" :size="15" />
        <span class="muted">
          {{ document.page_count }} pages ·
          <strong class="soft">{{ selected.length }} selected</strong>
        </span>
      </div>
      <div class="row tiny">
        <button class="btn ghost small" type="button" :disabled="!interactive" @click="selectVisible">
          {{ windowAllSelected ? "Deselect visible" : "Select visible" }}
        </button>
        <button class="btn ghost small" type="button" :disabled="!interactive || !selected.length" @click="emit('clear')">
          Clear
        </button>
        <div class="page-nav">
          <button class="icon-btn small" type="button" :disabled="!windowInfo.hasPrev" aria-label="Previous pages" @click="move(-1)">
            <Icon name="arrow-left" :size="15" />
          </button>
          <span class="tiny mono">{{ windowInfo.start }}–{{ windowInfo.end }}</span>
          <button class="icon-btn small" type="button" :disabled="!windowInfo.hasNext" aria-label="More pages" @click="move(1)">
            <Icon name="arrow-right" :size="15" />
          </button>
        </div>
      </div>
    </div>

    <p v-if="!interactive" class="field-hint preview-hint">
      Previews are available for every page. Choose <strong>Custom</strong> above to pick individual pages.
    </p>

    <div class="page-grid">
      <button
        v-for="page in windowInfo.pages"
        :key="page"
        class="page-card"
        :class="{ selected: selectedSet.has(page), interactive }"
        type="button"
        :aria-pressed="selectedSet.has(page)"
        :aria-label="`Page ${page}${selectedSet.has(page) ? ', selected' : ''}`"
        :disabled="!interactive"
        @click="toggle(page)"
      >
        <span class="thumb">
          <span v-if="!loaded.has(page) && !failed.has(page)" class="thumb-skeleton"></span>
          <img
            v-if="!failed.has(page)"
            :src="imageUrl(page)"
            :alt="`Preview of page ${page}`"
            loading="lazy"
            @load="loaded.add(page)"
            @error="failed.add(page)"
          />
          <span v-else class="thumb-fallback"><Icon name="file" :size="22" /></span>
          <span v-if="selectedSet.has(page)" class="check"><Icon name="check" :size="13" /></span>
        </span>
        <span class="page-label">Page {{ page }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.pages {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  padding: 13px;
}

.pages-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 9px;
}

.page-nav {
  display: flex;
  align-items: center;
  gap: 6px;
}

.preview-hint {
  margin: 0 0 12px;
}

.page-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(108px, 1fr));
  gap: 10px;
}

.page-card {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 7px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text-soft);
  transition: 0.15s ease;
  text-align: center;
}

.page-card.interactive:hover {
  border-color: var(--border-strong);
  transform: translateY(-1px);
}

.page-card.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
  color: var(--text);
}

.page-card:disabled {
  cursor: default;
  opacity: 1;
}

.thumb {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 0.71;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-3);
}

.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top;
  display: block;
}

.thumb-skeleton {
  position: absolute;
  inset: 0;
  background: linear-gradient(100deg, var(--surface-2) 30%, var(--surface-3) 50%, var(--surface-2) 70%);
  background-size: 220% 100%;
  animation: shimmer 1.4s linear infinite;
}

.thumb-fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--faint);
}

.check {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
}

.page-label {
  font-size: 11.5px;
  font-weight: 600;
}

@keyframes shimmer {
  to {
    background-position: -220% 0;
  }
}
</style>
