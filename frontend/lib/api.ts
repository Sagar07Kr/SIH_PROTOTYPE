import type {
  Analysis, DocumentInfo, Diff, Health, Job, SampleInfo, Validation,
  VersionPayload, Version,
} from "@/types/api";

export class ApiFailure extends Error {
  code: string;
  retryable: boolean;
  detail?: unknown;
  constructor(code: string, message: string, retryable = false, detail?: unknown) {
    super(message);
    this.code = code;
    this.retryable = retryable;
    this.detail = detail;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    let message = res.statusText || "Request failed";
    let retryable = res.status >= 500;
    let detail: unknown;
    try {
      const body = await res.json();
      code = body.code ?? code;
      message = body.message ?? message;
      retryable = Boolean(body.retryable);
      detail = body.detail;
    } catch {
      /* the server did not send a typed error; keep the status text */
    }
    throw new ApiFailure(code, message, retryable, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => call<Health>("/api/health"),
  samples: () => call<{ samples: SampleInfo[] }>("/api/samples"),

  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<DocumentInfo>("/api/documents", { method: "POST", body: form });
  },
  fromSample: (name: string) =>
    call<DocumentInfo>(`/api/documents/from-sample?name=${encodeURIComponent(name)}`,
      { method: "POST" }),
  analyze: (documentId: string) =>
    call<{ document: DocumentInfo; analysis: Analysis }>(
      `/api/documents/${documentId}/analyze`, { method: "POST" }),
  document: (documentId: string) =>
    call<{ document: DocumentInfo; analysis: Analysis }>(
      `/api/documents/${documentId}`),

  createProject: (name: string) =>
    call<{ id: string; name: string }>("/api/projects",
      { method: "POST", body: JSON.stringify({ name }) }),
  translate: (projectId: string, body: {
    document_id: string; target_lang: string; style?: string;
    glossary?: Record<string, string>;
    options?: Record<string, boolean>;
  }) => call<{ job_id: string; status: string }>(
    `/api/projects/${projectId}/translate`,
    { method: "POST", body: JSON.stringify(body) }),

  job: (jobId: string) => call<Job>(`/api/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    call<{ cancelled: boolean }>(`/api/jobs/${jobId}`, { method: "DELETE" }),

  version: (versionId: string) =>
    call<VersionPayload>(`/api/versions/${versionId}`),
  validate: (versionId: string) =>
    call<Validation>(`/api/versions/${versionId}/validate`, { method: "POST" }),
  diff: (a: string, b: string) => call<Diff>(`/api/versions/${a}/diff/${b}`),
  projectVersions: (projectId: string) =>
    call<{ versions: Version[] }>(`/api/projects/${projectId}/versions`),
  audit: (projectId: string) =>
    call<{ events: { id: string; event: string; payload: Record<string, unknown>; at: string }[] }>(
      `/api/projects/${projectId}/audit`),

  editSegment: (segmentId: string, text: string) =>
    call<{ version: Version; validation: Validation }>(
      `/api/segments/${segmentId}`,
      { method: "PATCH", body: JSON.stringify({ text }) }),
  regenerateSegment: (segmentId: string) =>
    call<{ version: Version; validation: Validation }>(
      `/api/segments/${segmentId}/regenerate`, { method: "POST" }),
  regeneratePage: (versionId: string, page: number) =>
    call<{ version: Version; validation: Validation }>(
      `/api/versions/${versionId}/pages/${page}/regenerate`, { method: "POST" }),

  documentPdfUrl: (documentId: string) => `/api/documents/${documentId}/pdf`,
  documentPageUrl: (documentId: string, page: number, dpi = 110) =>
    `/api/documents/${documentId}/pages/${page}/render?dpi=${dpi}`,
  versionPageUrl: (versionId: string, page: number, dpi = 110) =>
    `/api/versions/${versionId}/pages/${page}/render?dpi=${dpi}`,
  versionPdfUrl: (versionId: string) => `/api/versions/${versionId}/pdf`,
  exportUrl: (versionId: string) => `/api/versions/${versionId}/export`,
};

export function severityClass(severity: string): string {
  if (severity === "ERROR") return "text-bad";
  if (severity === "WARNING") return "text-warn";
  return "text-muted";
}

export const RUNG_LABELS: Record<number, string> = {
  0: "original",
  1: "leading tightened",
  2: "tracking tightened",
  3: "font reduced",
  4: "box grown down",
  5: "box grown into margin",
  6: "overflow",
};
