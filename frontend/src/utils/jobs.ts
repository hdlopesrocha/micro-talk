import type { JobArtifacts, JobState, JobStatus } from "../api/types";

export const ACTIVE_STATUSES = new Set<JobStatus>(["queued", "running", "cancelling"]);
export const TERMINAL_STATUSES = new Set<JobStatus>([
  "completed",
  "failed",
  "cancelled",
]);

export function isActive(job: JobState | null | undefined): boolean {
  return !!job && ACTIVE_STATUSES.has(job.status);
}

export function isTerminal(job: JobState | null | undefined): boolean {
  return !!job && TERMINAL_STATUSES.has(job.status);
}

export function statusTone(status: JobStatus | string): string {
  switch (status) {
    case "completed":
      return "success";
    case "failed":
      return "danger";
    case "cancelled":
      return "amber";
    case "cancelling":
      return "amber";
    case "running":
      return "accent";
    default:
      return "";
  }
}

export function outputLabels(artifacts: JobArtifacts | null | undefined): string[] {
  if (!artifacts) return [];
  const labels: string[] = [];
  if (artifacts.translation) labels.push("Translation");
  if (artifacts.summary) labels.push("Summary");
  if (artifacts.subtitles) labels.push("Subtitles");
  if (artifacts.audio) labels.push("Audio");
  if (artifacts.zip) labels.push("ZIP");
  return labels;
}

export function sourceLabel(job: JobState): string {
  const source = job.source || {};
  if (source.filename) {
    if (source.type === "pdf" && source.page_count) {
      const selected = source.selected_pages?.length || 0;
      return `${source.filename} · ${selected || source.page_count} page(s)`;
    }
    return String(source.filename);
  }
  if (job.config?.input?.type === "text") {
    return `${(job.config.input.text_characters || 0).toLocaleString()} characters of pasted text`;
  }
  return "Document";
}

export function stepIcon(status: string): string {
  switch (status) {
    case "completed":
      return "check";
    case "failed":
      return "alert";
    case "cancelled":
      return "x";
    case "skipped":
      return "arrow-right";
    case "running":
      return "refresh";
    default:
      return "clock";
  }
}

export function formatRelative(timestamp: number | null | undefined): string {
  if (!timestamp) return "—";
  const delta = Date.now() / 1000 - timestamp;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} h ago`;
  if (delta < 7 * 86400) return `${Math.floor(delta / 86400)} d ago`;
  return new Date(timestamp * 1000).toLocaleDateString();
}
