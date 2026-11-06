<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    name: string;
    size?: number | string;
    label?: string;
  }>(),
  { size: 20, label: "" },
);

const paths: Record<string, string> = {
  sun: "M12 3v2m0 14v2M5.6 5.6 7 7m10 10 1.4 1.4M3 12h2m14 0h2M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  moon: "M20.5 14.3A8.5 8.5 0 0 1 9.7 3.5a8.5 8.5 0 1 0 10.8 10.8Z",
  upload: "M12 16V4m0 0 4 4m-4-4L8 8M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6",
  text: "M4 6h16M4 12h16M4 18h10",
  translate: "M4 5h9M8.5 3v2M6 5c.7 4 3 7 7 8.5M11 5c-.6 4-3 7-7 8.5M14 20l4-10 4 10m-6.7-3h5.4",
  sparkles: "m12 3 1.3 4.2L17.5 8.5l-4.2 1.3L12 14l-1.3-4.2L6.5 8.5l4.2-1.3L12 3Zm6.5 10 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2ZM5 14l.9 2.1L8 17l-2.1.9L5 20l-.9-2.1L2 17l2.1-.9L5 14Z",
  volume: "M4 9v6h4l5 4V5L8 9H4Zm12.5-1.5a6 6 0 0 1 0 9M19 5a10 10 0 0 1 0 14",
  play: "M8 5v14l11-7L8 5Z",
  pause: "M8 5h3v14H8V5Zm5 0h3v14h-3V5Z",
  download: "M12 4v12m0 0 4-4m-4 4-4-4M4 19h16",
  copy: "M9 9h10v12H9V9ZM5 15H4V3h12v1",
  check: "m5 13 4 4L19 7",
  x: "M6 6l12 12M18 6 6 18",
  settings:
    "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Zm8-3.2a8 8 0 0 0-.2-1.7l2-1.5-2-3.5-2.4 1a8 8 0 0 0-2.9-1.7L14 2h-4l-.5 2.6a8 8 0 0 0-2.9 1.7l-2.4-1-2 3.5 2 1.5A8 8 0 0 0 4 12c0 .6.1 1.2.2 1.7l-2 1.5 2 3.5 2.4-1a8 8 0 0 0 2.9 1.7L10 22h4l.5-2.6a8 8 0 0 0 2.9-1.7l2.4 1 2-3.5-2-1.5c.1-.5.2-1.1.2-1.7Z",
  refresh: "M20 11a8 8 0 0 0-14.9-3M4 4v4h4m-4 5a8 8 0 0 0 14.9 3M20 20v-4h-4",
  trash: "M4 7h16M9 7V4h6v3m-9 0 1 14h10l1-14M10 11v6m4-6v6",
  "arrow-left": "M19 12H5m0 0 6-6m-6 6 6 6",
  "arrow-right": "M5 12h14m0 0-6-6m6 6-6 6",
  alert: "M12 3 2.5 20h19L12 3Zm0 6v5m0 3h.01",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2",
  chevron: "m9 6 6 6-6 6",
  library: "M4 4h5v16H4V4Zm7 0h5v16h-5V4Zm8 1 3 14",
  plus: "M12 5v14M5 12h14",
  tools: "M14.7 6.3a4 4 0 0 0-5 5L4 17l3 3 5.7-5.7a4 4 0 0 0 5-5l-2.5 2.5-2.8-2.8 2.3-2.7Z",
  external: "M14 4h6v6m0-6-9 9M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5",
  ear: "M6 10a6 6 0 1 1 12 0c0 4-4 4-4 7a2 2 0 0 1-4 0M9 10a3 3 0 0 1 6 0",
  layers: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-9v5m0-8h.01",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm-9-9h18M12 3c2.5 2.5 3.5 5.5 3.5 9s-1 6.5-3.5 9c-2.5-2.5-3.5-5.5-3.5-9S9.5 5.5 12 3Z",
  scissors: "M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm0 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm12-18L6 15m12 6L6 9",
  scan: "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M7 12h10",
  link: "M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1m-1 6a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1",
  captions: "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm2 6h4m-4 3h6m2-3h4m-4 3h3",
  "skip-back": "M19 5v14l-9-7 9-7ZM5 5v14",
  "skip-forward": "M5 5v14l9-7-9-7Zm14 0v14",
  "file-text": "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Zm0 0v6h6M8 13h8M8 17h5",
  plug: "M9 3v5m6-5v5M6 8h12v3a6 6 0 0 1-12 0V8Zm6 9v4",
  bookmark: "M6 3h12v18l-6-4-6 4V3Z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
};

const path = computed(() => paths[props.name] || paths.info);
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    :aria-hidden="label ? undefined : 'true'"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    class="icon"
  >
    <path :d="path" />
  </svg>
</template>

<style scoped>
.icon {
  display: inline-block;
  flex: 0 0 auto;
  vertical-align: middle;
}
</style>
