import type {
  Catalog,
  CreateJobRequest,
  CueResponse,
  DirectTTSResult,
  JobListResponse,
  JobState,
  LanguageCode,
  OutputLanguage,
  PDFDocument,
  SummaryResult,
  TTSTimeline,
  TranslationResult,
} from "./types";
import { authHeaders, authRequired, notifyUnauthorized, withToken } from "./auth";

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(
    message: string,
    options: { status?: number; code?: string; details?: Record<string, unknown> } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status || 0;
    this.code = options.code || "request_failed";
    this.details = options.details || {};
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status}).`;
  let code = "request_failed";
  let details: Record<string, unknown> = {};
  try {
    const payload = await response.json();
    if (payload?.error) {
      message = String(payload.error.message || message);
      code = String(payload.error.code || code);
      details = payload.error.details || {};
    }
  } catch {
    // Non-JSON errors (for example a proxy timeout page) keep the fallback text.
  }
  return new ApiError(message, { status: response.status, code, details });
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  for (const [key, value] of Object.entries(authHeaders())) headers.set(key, value);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(
      "The local server could not be reached. Check that Docker Compose is running.",
      { code: "network_error" },
    );
  }
  if (response.status === 401) {
    notifyUnauthorized();
    throw await parseError(response);
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as T;
}

function jsonBody(payload: unknown): string {
  return JSON.stringify(payload);
}

export const api = {
  async authStatus(): Promise<{ auth_required: boolean; version: string }> {
    return apiFetch("/api/v1/auth/status");
  },

  async checkToken(): Promise<void> {
    await apiFetch("/api/v1/auth/check");
  },

  async catalog(): Promise<Catalog> {
    return apiFetch("/api/v1/catalog");
  },

  async uploadDocument(file: File, onProgress?: (percent: number) => void): Promise<PDFDocument> {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", "/api/v1/documents");
      for (const [key, value] of Object.entries(authHeaders())) {
        request.setRequestHeader(key, value);
      }
      request.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      request.onerror = () =>
        reject(new ApiError("The PDF upload failed because the server is unreachable.", {
          code: "network_error",
        }));
      request.onload = () => {
        let payload: any = null;
        try {
          payload = JSON.parse(request.responseText || "{}");
        } catch {
          payload = null;
        }
        if (request.status >= 200 && request.status < 300 && payload) {
          resolve(payload as PDFDocument);
          return;
        }
        if (request.status === 401) notifyUnauthorized();
        reject(
          new ApiError(
            String(payload?.error?.message || `The PDF upload failed (${request.status}).`),
            {
              status: request.status,
              code: String(payload?.error?.code || "upload_failed"),
              details: payload?.error?.details || {},
            },
          ),
        );
      };
      const form = new FormData();
      form.append("file", file, file.name);
      request.send(form);
    });
  },

  async deleteDocument(documentId: string): Promise<void> {
    await apiFetch(`/api/v1/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
  },

  documentPreviewUrl(documentId: string, page: number, width = 320): string {
    return withToken(
      `/api/v1/documents/${encodeURIComponent(documentId)}/pages/${page}/preview.png?width=${width}`,
    );
  },

  async createJob(payload: CreateJobRequest): Promise<JobState> {
    return apiFetch("/api/v1/jobs", { method: "POST", body: jsonBody(payload) });
  },

  async getJob(jobId: string): Promise<JobState> {
    return apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
  },

  async listJobs(options: { limit?: number; offset?: number; status?: string } = {}): Promise<JobListResponse> {
    const params = new URLSearchParams();
    params.set("limit", String(options.limit ?? 20));
    params.set("offset", String(options.offset ?? 0));
    if (options.status) params.set("status", options.status);
    return apiFetch(`/api/v1/jobs?${params.toString()}`);
  },

  async cancelJob(jobId: string): Promise<JobState> {
    return apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
  },

  async deleteJob(jobId: string, deleteSource = false): Promise<void> {
    const query = deleteSource ? "?delete_source=true" : "";
    await apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}${query}`, { method: "DELETE" });
  },

  async jobText(jobId: string, kind: string): Promise<string> {
    return apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/text/${encodeURIComponent(kind)}`);
  },

  async saveJobText(
    jobId: string,
    kind: string,
    text: string,
  ): Promise<{ artifact: Record<string, unknown>; warning: string | null }> {
    return apiFetch(
      `/api/v1/jobs/${encodeURIComponent(jobId)}/text/${encodeURIComponent(kind)}`,
      { method: "PATCH", body: jsonBody({ text }) },
    );
  },

  async cues(jobId: string): Promise<CueResponse> {
    return apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/cues`);
  },

  eventUrl(jobId: string): string {
    return withToken(`/api/v1/jobs/${encodeURIComponent(jobId)}/events`);
  },

  downloadUrl(jobId: string, kind: string, inline = false): string {
    const query = inline ? "?inline=true" : "";
    return withToken(
      `/api/v1/jobs/${encodeURIComponent(jobId)}/downloads/${encodeURIComponent(kind)}${query}`,
    );
  },

  async directTranslate(payload: {
    text: string;
    source_language: LanguageCode;
    target_language: OutputLanguage;
  }): Promise<TranslationResult> {
    return apiFetch("/api/v1/direct/translate", {
      method: "POST",
      body: jsonBody(payload),
    });
  },

  async directSummarize(payload: {
    text: string;
    language: OutputLanguage;
    source_language: LanguageCode;
    length: "short" | "medium" | "long";
  }): Promise<SummaryResult> {
    return apiFetch("/api/v1/direct/summarize", {
      method: "POST",
      body: jsonBody(payload),
    });
  },

  async directTts(payload: {
    text: string;
    voice: string;
    language?: string;
    speed: number;
    format: "mp3" | "wav";
    normalize_audio: boolean;
  }): Promise<DirectTTSResult> {
    return apiFetch("/api/v1/direct/tts", { method: "POST", body: jsonBody(payload) });
  },

  async directTtsStatus(jobId: string): Promise<DirectTTSResult> {
    return apiFetch(`/api/v1/direct/tts/${encodeURIComponent(jobId)}`);
  },

  async directTtsCancel(jobId: string): Promise<DirectTTSResult> {
    return apiFetch(`/api/v1/direct/tts/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  },

  async directTtsTimeline(jobId: string): Promise<TTSTimeline> {
    return apiFetch(`/api/v1/direct/tts/${encodeURIComponent(jobId)}/timeline`);
  },

  directAudioUrl(jobId: string, wav = false, inline = true): string {
    return withToken(
      `/api/v1/direct/tts/${encodeURIComponent(jobId)}/${wav ? "wav" : "audio"}?inline=${inline ? "true" : "false"}`,
    );
  },
};

export function handleApiError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "An unexpected error occurred.";
}

export { authRequired };
