import type { Cue } from "../api/types";

export function formatClock(seconds: number): string {
  const value = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const whole = Math.floor(value);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function cueAtTime(cues: Cue[], seconds: number): Cue | null {
  if (!cues.length) return null;
  let low = 0;
  let high = cues.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    const cue = cues[middle];
    if (seconds < cue.start) high = middle - 1;
    else if (seconds >= cue.end) low = middle + 1;
    else return cue;
  }
  return null;
}

export function cueIndexAtTime(cues: Cue[], seconds: number): number {
  const cue = cueAtTime(cues, seconds);
  return cue ? cues.findIndex((item) => item.index === cue.index) : -1;
}
