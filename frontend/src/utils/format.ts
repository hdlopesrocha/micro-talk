import type { JobStatus } from "../api/types";

export function formatBytes(bytes: number | null | undefined): string {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

export function formatDate(timestamp: number | null | undefined): string {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(timestamp * 1000));
}

export function formatDuration(seconds: number | null | undefined): string {
  const value = Number(seconds) || 0;
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(value / 60);
  const remainder = Math.round(value % 60);
  return `${minutes}m ${remainder}s`;
}

export function countWords(text: string | null | undefined): number {
  if (!text) return 0;
  // Keep displayed counts aligned with the backend: count document words, not
  // structural PDF page markers such as "--- Page 1 ---".
  const content = text.replace(/^[ \t]*---[ \t]*Page[ \t]+\d+[ \t]*---[ \t]*$/gmu, " ");
  return (content.match(/[\p{L}\p{N}_](?:[\p{L}\p{N}_'-]*[\p{L}\p{N}_])?/gu) || []).length;
}

export function statusLabel(status: JobStatus | string): string {
  const labels: Record<string, string> = {
    queued: "Queued",
    running: "Running",
    cancelling: "Cancelling",
    completed: "Complete",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return labels[status] || status;
}

export function elapsedLabel(start: number | null, end: number | null): string {
  if (!start) return "—";
  const finish = end || Date.now() / 1000;
  return formatDuration(Math.max(0, finish - start));
}

export function languageLabel(code: string | null | undefined): string {
  const labels: Record<string, string> = {
    auto: "Detect automatically",
    pt: "Portuguese",
    "pt-PT": "Portuguese (Portugal)",
    "pt-BR": "Portuguese (Brazil)",
    en: "English",
    fr: "French",
    "fr-FR": "French (France)",
    und: "Unknown",
  };
  return code ? labels[code] || code : "—";
}
