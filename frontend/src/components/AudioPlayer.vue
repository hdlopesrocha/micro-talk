<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import type { Cue } from "../api/types";
import { cueAtTime, formatClock } from "../utils/subtitles";
import Icon from "./Icon.vue";

const props = withDefaults(
  defineProps<{
    src: string;
    cues?: Cue[];
    title?: string;
    format?: string;
    durationHint?: number | null;
  }>(),
  {
    cues: () => [],
    title: "Generated audio",
    format: "",
    durationHint: null,
  },
);

const audio = ref<HTMLAudioElement | null>(null);
const cueList = ref<HTMLElement | null>(null);
const playing = ref(false);
const currentTime = ref(0);
const duration = ref(Number(props.durationHint) || 0);
const buffered = ref(0);
const volume = ref(0.9);
const muted = ref(false);
const speed = ref(1);
const audioError = ref("");
const follow = ref(true);
const transcriptOpen = ref(true);
let animationFrame = 0;

const activeCue = computed<Cue | null>(() => cueAtTime(props.cues, currentTime.value));
const activeIndex = computed(() =>
  activeCue.value
    ? props.cues.findIndex((cue) => cue.index === activeCue.value?.index)
    : -1,
);
const progressPercent = computed(() =>
  duration.value > 0 ? Math.min(100, (currentTime.value / duration.value) * 100) : 0,
);
const bufferedPercent = computed(() =>
  duration.value > 0 ? Math.min(100, (buffered.value / duration.value) * 100) : 0,
);
const speedOptions = [0.75, 1, 1.25, 1.5, 2];
const canPlay = computed(() => !!props.src && !audioError.value);

watch(
  () => props.src,
  () => {
    audioError.value = "";
    playing.value = false;
    currentTime.value = 0;
    duration.value = Number(props.durationHint) || 0;
    buffered.value = 0;
    if (audio.value) {
      audio.value.load();
      audio.value.volume = volume.value;
      audio.value.muted = muted.value;
    }
  },
);

watch(
  () => props.durationHint,
  (value) => {
    if (!duration.value && value) duration.value = Number(value);
  },
);

watch(
  () => [activeIndex.value, follow.value] as const,
  async ([index, shouldFollow]) => {
    if (!shouldFollow || index < 0) return;
    await nextTick();
    const container = cueList.value;
    if (!container) return;
    const item = container.querySelector<HTMLElement>(`.cue-item[data-cue="${activeCue.value?.index}"]`);
    if (!item) return;
    const target = item.offsetTop - container.clientHeight / 2 + item.clientHeight / 2;
    container.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
  },
);

function syncTime() {
  if (!audio.value) return;
  currentTime.value = audio.value.currentTime;
  if (playing.value) animationFrame = requestAnimationFrame(syncTime);
}

function toggle() {
  if (!audio.value || !canPlay.value) return;
  if (audio.value.paused) {
    audio.value
      .play()
      .then(() => {
        playing.value = true;
        cancelAnimationFrame(animationFrame);
        animationFrame = requestAnimationFrame(syncTime);
      })
      .catch(() => {
        audioError.value = "The browser blocked audio playback. Press play again or download the file.";
      });
  } else {
    audio.value.pause();
    playing.value = false;
    cancelAnimationFrame(animationFrame);
  }
}

function seek(value: number) {
  if (!audio.value || !Number.isFinite(value)) return;
  const target = Math.min(Math.max(0, value), duration.value || value);
  audio.value.currentTime = target;
  currentTime.value = target;
}

function onSeekInput(event: Event) {
  seek(Number((event.target as HTMLInputElement).value));
}

function skip(seconds: number) {
  seek(currentTime.value + seconds);
}

function onLoadedMetadata() {
  if (!audio.value) return;
  if (Number.isFinite(audio.value.duration) && audio.value.duration > 0) {
    duration.value = audio.value.duration;
  }
  audio.value.playbackRate = speed.value;
  audio.value.volume = volume.value;
  audio.value.muted = muted.value;
}

function onTimeUpdate() {
  if (!audio.value) return;
  currentTime.value = audio.value.currentTime;
  if (!playing.value) {
    playing.value = !audio.value.paused;
    if (playing.value) animationFrame = requestAnimationFrame(syncTime);
  }
}

function onProgress() {
  if (!audio.value || !audio.value.buffered.length) return;
  const end = audio.value.buffered.end(audio.value.buffered.length - 1);
  buffered.value = end;
}

function onEnded() {
  playing.value = false;
  cancelAnimationFrame(animationFrame);
}

function onError() {
  playing.value = false;
  audioError.value = "The audio stream could not be played. Try downloading the file.";
}

function setVolume(event: Event) {
  const value = Number((event.target as HTMLInputElement).value);
  volume.value = value;
  muted.value = value === 0;
  if (audio.value) {
    audio.value.volume = value;
    audio.value.muted = muted.value;
  }
}

function toggleMute() {
  muted.value = !muted.value;
  if (audio.value) audio.value.muted = muted.value;
  if (!muted.value && volume.value === 0) {
    volume.value = 0.8;
    if (audio.value) audio.value.volume = 0.8;
  }
}

function setSpeed(value: number) {
  speed.value = value;
  if (audio.value) audio.value.playbackRate = value;
}

function onKeydown(event: KeyboardEvent) {
  if (event.target instanceof HTMLInputElement) return;
  if (event.code === "Space") {
    event.preventDefault();
    toggle();
  } else if (event.code === "ArrowLeft") {
    event.preventDefault();
    skip(-10);
  } else if (event.code === "ArrowRight") {
    event.preventDefault();
    skip(10);
  }
}

onBeforeUnmount(() => cancelAnimationFrame(animationFrame));
</script>

<template>
  <div class="player" tabindex="0" @keydown="onKeydown">
    <audio
      ref="audio"
      :src="src"
      preload="metadata"
      playsinline
      @loadedmetadata="onLoadedMetadata"
      @durationchange="onLoadedMetadata"
      @timeupdate="onTimeUpdate"
      @progress="onProgress"
      @play="playing = true"
      @pause="playing = false"
      @ended="onEnded"
      @error="onError"
    ></audio>

    <div class="player-head">
      <div class="player-mark"><Icon name="volume" :size="20" /></div>
      <div class="grow">
        <strong class="truncate">{{ title }}</strong>
        <div class="tiny muted">
          {{ format ? format.toUpperCase() : "Audio" }} · streamed with HTTP Range support
        </div>
      </div>
      <span class="badge teal"><span class="status-dot"></span> Ready</span>
    </div>

    <div class="caption-stage" :class="{ silent: !activeCue }" aria-live="polite">
      <span v-if="activeCue">{{ activeCue.text }}</span>
      <span v-else class="tiny muted">Subtitles follow the generated timeline.</span>
    </div>

    <div v-if="audioError" class="alert error compact" role="alert">
      <Icon name="alert" :size="17" /><span>{{ audioError }}</span>
    </div>

    <div class="seek-wrap">
      <span class="time mono">{{ formatClock(currentTime) }}</span>
      <div class="seek-track">
        <div class="seek-buffered" :style="{ width: `${bufferedPercent}%` }"></div>
        <div class="seek-played" :style="{ width: `${progressPercent}%` }"></div>
        <input
          class="seek"
          type="range"
          min="0"
          :max="duration || 0"
          step="0.05"
          :value="currentTime"
          :disabled="!canPlay"
          aria-label="Audio position"
          @input="onSeekInput"
        />
      </div>
      <span class="time mono">{{ formatClock(duration) }}</span>
    </div>

    <div class="controls">
      <div class="row controls-main">
        <button class="icon-btn small" type="button" title="Back 10 seconds" aria-label="Back 10 seconds" :disabled="!canPlay" @click="skip(-10)">
          <Icon name="skip-back" :size="16" />
        </button>
        <button
          class="play-btn"
          type="button"
          :aria-label="playing ? 'Pause audio' : 'Play audio'"
          :disabled="!canPlay"
          @click="toggle"
        >
          <Icon :name="playing ? 'pause' : 'play'" :size="20" />
        </button>
        <button class="icon-btn small" type="button" title="Forward 10 seconds" aria-label="Forward 10 seconds" :disabled="!canPlay" @click="skip(10)">
          <Icon name="skip-forward" :size="16" />
        </button>
        <div class="volume-group">
          <button class="icon-btn small" type="button" :aria-label="muted ? 'Unmute' : 'Mute'" :title="muted ? 'Unmute' : 'Mute'" @click="toggleMute">
            <Icon name="volume" :size="16" />
          </button>
          <input
            class="volume"
            type="range"
            min="0"
            max="1"
            step="0.01"
            :value="muted ? 0 : volume"
            aria-label="Volume"
            @input="setVolume"
          />
        </div>
      </div>
      <div class="row controls-side">
        <label class="speed">
          <span class="sr-only">Playback speed</span>
          <select :value="speed" class="select tiny-select" @change="setSpeed(Number(($event.target as HTMLSelectElement).value))">
            <option v-for="value in speedOptions" :key="value" :value="value">{{ value }}×</option>
          </select>
        </label>
        <button
          v-if="cues.length"
          class="btn ghost small"
          type="button"
          :class="{ active: follow }"
          @click="follow = !follow"
        >
          <Icon name="captions" :size="14" /> Follow
        </button>
        <button
          v-if="cues.length"
          class="btn ghost small"
          type="button"
          @click="transcriptOpen = !transcriptOpen"
        >
          <Icon name="list" :size="14" /> {{ transcriptOpen ? "Hide" : "Show" }} transcript
        </button>
      </div>
    </div>

    <div v-if="transcriptOpen && cues.length" class="transcript">
      <div class="transcript-head">
        <span class="field-label">Synchronized transcript</span>
        <span class="tiny muted">{{ cues.length }} cues</span>
      </div>
      <div ref="cueList" class="cues">
        <button
          v-for="cue in cues"
          :key="cue.index"
          class="cue-item"
          :class="{ active: cue.index === activeCue?.index }"
          type="button"
          :data-cue="cue.index"
          @click="seek(cue.start)"
        >
          <span class="cue-time mono">{{ formatClock(cue.start) }}</span>
          <span class="cue-text">{{ cue.text }}</span>
        </button>
      </div>
    </div>
    <p v-else-if="transcriptOpen && !cues.length" class="tiny muted no-cues">
      No subtitle cues are available for this audio.
    </p>
  </div>
</template>

<style scoped>
.player {
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background:
    radial-gradient(circle at 100% 0, var(--accent-soft), transparent 42%),
    var(--bg-soft);
}

.player:focus-visible {
  outline-offset: 4px;
}

.player-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.player-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
  flex: 0 0 auto;
}

.caption-stage {
  min-height: 72px;
  display: grid;
  place-items: center;
  text-align: center;
  padding: 14px 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  color: var(--text);
  font-size: clamp(15px, 2vw, 19px);
  font-weight: 550;
  line-height: 1.45;
  letter-spacing: -0.01em;
}

.caption-stage.silent {
  color: var(--muted);
  font-weight: 400;
}

.seek-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.time {
  font-size: 11.5px;
  color: var(--muted);
  min-width: 39px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.seek-track {
  position: relative;
  height: 18px;
  flex: 1;
  display: flex;
  align-items: center;
}

.seek-track::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  height: 6px;
  border-radius: 100px;
  background: var(--surface-3);
}

.seek-buffered,
.seek-played {
  position: absolute;
  left: 0;
  height: 6px;
  border-radius: 100px;
  pointer-events: none;
}

.seek-buffered {
  background: var(--border-strong);
}

.seek-played {
  background: linear-gradient(90deg, var(--accent), var(--teal));
}

.seek {
  position: relative;
  z-index: 1;
  width: 100%;
  margin: 0;
  appearance: none;
  background: transparent;
  height: 18px;
  cursor: pointer;
}

.seek::-webkit-slider-thumb {
  appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  border: 3px solid var(--accent);
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.4);
}

.seek::-moz-range-thumb {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  border: 3px solid var(--accent);
}

.controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.controls-main,
.controls-side {
  gap: 8px;
}

.play-btn {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-strong));
  color: #fff;
  box-shadow: 0 8px 22px rgba(91, 124, 242, 0.28);
  transition: transform 0.15s ease, opacity 0.15s ease;
}

.play-btn:hover:not(:disabled) {
  transform: scale(1.04);
}

.play-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.volume-group {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: 6px;
}

.volume {
  width: 74px;
  accent-color: var(--accent);
}

.speed .select {
  min-height: 32px;
  padding: 5px 28px 5px 9px;
  font-size: 12px;
}

.tiny-select {
  width: auto;
}

.btn.ghost.active {
  color: var(--accent);
  border-color: var(--accent);
}

.transcript {
  border-top: 1px solid var(--border);
  padding-top: 13px;
}

.transcript-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.cues {
  position: relative;
  max-height: 280px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-right: 4px;
}

.cue-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: var(--text-soft);
  text-align: left;
  transition: 0.14s ease;
}

.cue-item:hover {
  background: var(--surface-2);
  color: var(--text);
}

.cue-item.active {
  border-color: color-mix(in srgb, var(--accent) 36%, transparent);
  background: var(--accent-soft);
  color: var(--text);
}

.cue-time {
  color: var(--muted);
  font-size: 11px;
  min-width: 40px;
  padding-top: 2px;
  font-variant-numeric: tabular-nums;
}

.cue-text {
  font-size: 13px;
  line-height: 1.45;
}

.no-cues {
  margin: 0;
}

.alert.compact {
  padding: 10px 12px;
  font-size: 12.5px;
}

@media (max-width: 620px) {
  .player {
    padding: 14px;
  }
  .controls {
    align-items: stretch;
    flex-direction: column;
  }
  .controls-main {
    justify-content: center;
  }
  .controls-side {
    justify-content: center;
  }
  .volume-group {
    margin-left: 0;
  }
  .volume {
    width: 60px;
  }
  .caption-stage {
    font-size: 15px;
    padding: 12px;
  }
}
</style>
