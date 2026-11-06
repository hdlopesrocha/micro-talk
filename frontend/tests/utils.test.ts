import { describe, expect, it } from "vitest";
import {
  countWords,
  elapsedLabel,
  formatBytes,
  formatDuration,
  languageLabel,
  statusLabel,
} from "../src/utils/format";
import {
  isActive,
  isTerminal,
  outputLabels,
  sourceLabel,
  statusTone,
  stepIcon,
} from "../src/utils/jobs";
import type { JobState } from "../src/api/types";

describe("format helpers", () => {
  it("formats bytes across units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(2 * 1024 ** 2)).toBe("2.0 MB");
  });

  it("formats durations", () => {
    expect(formatDuration(0)).toBe("0.0s");
    expect(formatDuration(9.4)).toBe("9.4s");
    expect(formatDuration(65)).toBe("1m 5s");
  });

  it("counts words without structural page markers", () => {
    expect(countWords("")).toBe(0);
    expect(countWords(null)).toBe(0);
    expect(countWords("--- Page 1 ---\n\nHello world, don't stop.")).toBe(4);
    expect(countWords("--- Page 1 ---\n\nHello.\n\n--- Page 2 ---\n\nWorld.")).toBe(2);
    expect(countWords("palavra ação coração")).toBe(3);
  });

  it("labels statuses and languages", () => {
    expect(statusLabel("completed")).toBe("Complete");
    expect(statusLabel("custom")).toBe("custom");
    expect(languageLabel("pt-BR")).toBe("Portuguese (Brazil)");
    expect(languageLabel(null)).toBe("—");
  });

  it("describes elapsed time without a start", () => {
    expect(elapsedLabel(null, null)).toBe("—");
    expect(elapsedLabel(100, 105)).toBe("5.0s");
  });
});

function job(overrides: Partial<JobState> = {}): JobState {
  return {
    id: "00000000-0000-4000-8000-000000000000",
    status: "running",
    title: "Document",
    progress: 40,
    message: "Working",
    created_at: 100,
    updated_at: 101,
    started_at: 100,
    finished_at: null,
    current_step: "translate",
    config: {
      title: "",
      input: {
        type: "text",
        document_id: null,
        text: null,
        language: "auto",
        start_page: null,
        end_page: null,
        selected_pages: null,
        ocr_enabled: false,
      },
      translation: { enabled: true, source_language: "auto", target_language: "en" },
      summary: { enabled: false, length: "medium", language: null },
      tts: { enabled: false, voice: "pt-PT-tugao", speed: 1, format: "mp3", normalize_audio: false, text_source: "content" },
    },
    steps: [],
    artifacts: {},
    warnings: [],
    source: {},
    languages: {},
    stats: {},
    error: null,
    cancel_requested: false,
    ...overrides,
  } as JobState;
}

describe("job helpers", () => {
  it("detects active and terminal states", () => {
    expect(isActive(job())).toBe(true);
    expect(isActive(job({ status: "completed" }))).toBe(false);
    expect(isTerminal(job({ status: "failed" }))).toBe(true);
    expect(isTerminal(null)).toBe(false);
  });

  it("maps statuses to visual tones and icons", () => {
    expect(statusTone("completed")).toBe("success");
    expect(statusTone("failed")).toBe("danger");
    expect(statusTone("cancelled")).toBe("amber");
    expect(stepIcon("completed")).toBe("check");
    expect(stepIcon("running")).toBe("refresh");
  });

  it("summarizes artifacts and source text", () => {
    const artifacts = {
      translation: { available: true } as never,
      audio: { available: true } as never,
    };
    expect(outputLabels(artifacts)).toEqual(["Translation", "Audio"]);
    expect(
      sourceLabel(
        job({
          config: {
            ...job().config,
            input: { ...job().config.input, text_characters: 1234 },
          },
        }),
      ),
    ).toBe("1,234 characters of pasted text");
  });
});
