export type JobStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

export type LanguageCode = "auto" | "pt" | "pt-PT" | "pt-BR" | "en" | "fr";
export type OutputLanguage = "en" | "pt-PT" | "pt-BR" | "fr";
export type SummaryLanguage = "en" | "pt-PT" | "pt-BR";

export interface PDFDocument {
  id: string;
  filename: string;
  size_bytes: number;
  page_count: number;
  title: string;
  author: string;
  created_at: number;
  likely_scanned: boolean;
  sampled_pages: number;
}

export interface Voice {
  id: string;
  name: string;
  language: string;
  language_name: string;
  engine: string;
  quality: string;
  sample_rate: number;
  license: string;
  description: string;
  gender: "female" | "male" | "unknown";
  pitch_supported: boolean;
  available: boolean;
  download_hint: string | null;
}

export interface ServiceHealth {
  status?: string;
  service?: string;
  version?: string;
  [key: string]: unknown;
}

export interface Catalog {
  translation_languages?: {
    source: string[];
    target: string[];
    pairs: Array<{ source: string; target: string }>;
  };
  translation_status?: {
    loaded_pair: string | null;
    supported_pairs: Record<string, boolean>;
    supported_routes?: Record<string, boolean>;
    model_ids?: Record<string, string>;
    unavailable?: boolean;
  };
  summary_lengths?: {
    lengths: Array<{ id: "short" | "medium" | "long"; label: string; max_tokens: number }>;
    languages: Array<{ id: string; label: string }>;
  };
  summary_status?: {
    available: Record<string, boolean>;
    model_ids?: Record<string, string>;
    unavailable?: boolean;
  };
  tts?: {
    voices: Voice[];
    engines: Array<{
      id: string;
      name: string;
      pitch_supported: boolean;
      device: string;
      description: string;
    }>;
    default_voice: string;
    pitch_supported: boolean;
    formats: string[];
  };
  health?: Record<string, ServiceHealth | null>;
  limits?: {
    max_upload_mb: number;
    max_text_chars: number;
    max_active_jobs: number;
  };
  defaults?: {
    translation_target: OutputLanguage;
    summary_length: "short" | "medium" | "long";
    tts_voice: string;
    tts_speed: number;
    tts_format: "mp3" | "wav";
  };
  auth_required?: boolean;
}

export interface InputConfig {
  type: "pdf" | "text";
  document_id: string | null;
  text: string | null;
  text_characters?: number;
  language: LanguageCode;
  start_page: number | null;
  end_page: number | null;
  selected_pages: number[] | null;
  ocr_enabled: boolean;
  ignore_notes: boolean;
  ignore_figure_descriptions: boolean;
}

export interface TranslationConfig {
  enabled: boolean;
  source_language: LanguageCode;
  target_language: OutputLanguage;
}

export interface SummaryConfig {
  enabled: boolean;
  length: "short" | "medium" | "long";
  language: SummaryLanguage | null;
}

export interface TTSConfig {
  enabled: boolean;
  voice: string;
  speed: number;
  format: "mp3" | "wav";
  normalize_audio: boolean;
  preserve_phrases: boolean;
  ignore_leading_numbers: boolean;
  text_source: "content" | "summary";
}

export interface JobConfig {
  title: string;
  input: InputConfig;
  translation: TranslationConfig;
  summary: SummaryConfig;
  tts: TTSConfig;
}

export interface JobStep {
  id: string;
  label: string;
  weight: number;
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "cancelled";
  progress: number;
  message: string;
  started_at: number | null;
  finished_at: number | null;
  error: { code: string; message: string } | null;
}

export interface TextArtifact {
  kind?: string;
  label?: string;
  filename: string;
  size_bytes: number;
  characters: number;
  words: number;
  edited_at?: number;
  available: boolean;
}

export interface AudioArtifact {
  kind?: string;
  media_id?: string;
  filename: string;
  format: string;
  content_type?: string;
  path: string;
  storage?: "media" | "tts";
  size_bytes: number | null;
  duration_seconds: number | null;
  normalized: boolean;
  available: boolean;
}

export interface SubtitleArtifact {
  filename: string;
  size_bytes: number;
  characters?: number;
  segments?: number;
  timing_method?: string;
  media_id?: string;
  available: boolean;
}

export interface ZipArtifact {
  filename: string;
  size_bytes: number;
  files: string[];
  available: boolean;
}

export interface JobArtifacts {
  extracted?: TextArtifact;
  translation?: TextArtifact;
  summary?: TextArtifact;
  audio?: AudioArtifact;
  wav?: AudioArtifact;
  subtitles?: SubtitleArtifact;
  captions?: SubtitleArtifact;
  zip?: ZipArtifact;
  [key: string]: unknown;
}

export interface TTSState {
  job_id: string;
  status: string;
  duration_seconds: number;
  sentences: number;
  audio_path: string;
  wav_path: string;
  format: string;
  timeline_method: string;
  timeline_segments?: number;
  voice?: string;
  voice_name?: string;
}

export interface JobState {
  id: string;
  status: JobStatus;
  title: string;
  progress: number;
  message: string;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  current_step: string | null;
  config: JobConfig;
  steps: JobStep[];
  artifacts: JobArtifacts;
  warnings: string[];
  source: {
    type?: string;
    document_id?: string;
    filename?: string;
    page_count?: number;
    selected_pages?: number[];
    ocr_enabled?: boolean;
    ignore_notes?: boolean;
    ignore_figure_descriptions?: boolean;
    likely_scanned?: boolean;
    size_bytes?: number;
  };
  languages: Record<string, string | boolean | null>;
  stats: Record<string, number | string | null>;
  error: { code: string; message: string; details?: Record<string, unknown> } | null;
  cancel_requested: boolean;
  tts?: TTSState;
}

export interface JobListResponse {
  jobs: JobState[];
  total: number;
  limit: number;
  offset: number;
}

export interface Cue {
  index: number;
  text: string;
  start: number;
  end: number;
}

export interface CueResponse {
  segments: Cue[];
}

export interface TranslationResult {
  source_language: string;
  source_language_name: string;
  target_language: string;
  target_language_name: string;
  detected_source_language: boolean;
  model: string;
  translated_text: string;
  pages: Array<{ page: number; text: string }>;
  stats: Record<string, number | string>;
  duration_seconds: number;
}

export interface SummaryResult {
  summary: string;
  source_language: string;
  language: string;
  length: string;
  translated: boolean;
  model: string;
  input_words: number;
  output_words: number;
  chunk_count: number;
  hierarchy_levels: number;
  duration_seconds: number;
  warnings: string[];
}

export interface DirectTTSResult {
  job_id: string;
  status: string;
  progress: number;
  message: string;
  duration_seconds?: number | null;
  completed_sentences?: number;
  sentence_count?: number;
  format?: string;
  audio_url?: string;
  wav_url?: string;
  gateway_audio_url: string;
  gateway_wav_url: string;
  gateway_timeline_url: string;
  error?: { code: string; message: string } | null;
}

export interface TTSTimeline {
  segments: Cue[];
  duration_seconds?: number;
  method?: string;
}

export interface CreateJobRequest {
  title: string;
  input: {
    type: "pdf" | "text";
    document_id?: string | null;
    text?: string | null;
    language: LanguageCode;
    start_page?: number | null;
    end_page?: number | null;
    selected_pages?: number[] | null;
    ocr_enabled: boolean;
    ignore_notes?: boolean;
    ignore_figure_descriptions?: boolean;
  };
  translation: TranslationConfig;
  summary: SummaryConfig;
  tts: TTSConfig;
}
