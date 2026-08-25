// Wire types mirroring backend/schemas and backend/api/routes.py.
export type Stage =
  | "UPLOADED" | "PARSING" | "OCR" | "LANG_DETECT" | "LAYOUT" | "SEGMENTING"
  | "TRANSLATING" | "RECONSTRUCTING" | "VALIDATING" | "GENERATING" | "DONE"
  | "FAILED";

export type StageStatus = "pending" | "active" | "done" | "skipped" | "failed";

export interface StageState { stage: Stage; status: StageStatus; ms?: number | null }

export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  detail?: Record<string, unknown> | null;
}

export interface JobProgress {
  stage: Stage;
  stages: StageState[];
  current_page?: number | null;
  total_pages: number;
  message?: string | null;
  error?: ApiError | null;
  segments_done: number;
  segments_total: number;
  version_id?: string | null;
}

export interface Job {
  id: string;
  project_id: string;
  document_id: string;
  target_lang: string;
  style: string;
  status: string;
  provider: string;
  progress: JobProgress;
  options: Record<string, unknown>;
  tokens: { input: number; output: number };
  timings_ms: { ocr: number; translate: number; reconstruct: number };
  started_at: string | null;
  finished_at: string | null;
}

export interface DocumentInfo {
  id: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  page_count: number;
  source_lang: string | null;
  source_lang_confidence: number;
  is_scanned: boolean;
  status: string;
  project_id: string | null;
  created_at: string;
}

export interface ParsedBlock {
  id: string;
  type: string;
  bbox: [number, number, number, number];
  text: string;
  column_index: number;
  reading_order: number;
  list_marker: string | null;
  ocr_confidence: number | null;
  translatable: boolean;
  protected: boolean;
  line_count: number;
  style: {
    font: string; size: number; bold: boolean; italic: boolean; mono: boolean;
    color: number[]; align: number; leading: number; rotation: number;
    line_height: number;
  };
}

export interface ParsedPage {
  index: number;
  width_pt: number;
  height_pt: number;
  rotation: number;
  is_scanned: boolean;
  modal_font_size: number;
  columns: [number, number][];
  blocks: ParsedBlock[];
  tables: { id: string; bbox: number[]; rows: number; cols: number; ruled: boolean;
    cell_count: number; header_rows: number }[];
  images: number[][];
  extractable_chars: number;
  ocr_mean_confidence: number | null;
}

export interface Analysis {
  page_count: number;
  source_lang: string;
  source_lang_confidence: number;
  is_scanned: boolean;
  element_counts: Record<string, number>;
  pages: ParsedPage[];
}

export interface Segment {
  id: string;
  element_id: string;
  page: number;
  type: string;
  list_marker: string | null;
  source: string;
  target: string;
  confidence: number;
  fit_rung: number | null;
  applied_font: string | null;
  applied_size: number | null;
  original_size: number | null;
  bbox: [number, number, number, number] | null;
  edited: boolean;
  status: string;
  issues: Issue[];
  placeholders: { tokens?: Record<string, string>; kinds?: Record<string, string> };
  font_substitution: FontSubstitution | null;
}

export interface Issue {
  code: string;
  severity: "ERROR" | "WARNING" | "INFO";
  message: string;
  page?: number;
  blocks?: string[];
  characters?: string[];
  excess_pt?: number;
  value?: number;
  fraction?: number;
  segment?: string;
}

export interface FontSubstitution {
  alias?: string; family?: string; original: string; replacement?: string;
  file?: string; reason?: string; size_factor?: number; weight?: number;
}

export interface Metric {
  key: string;
  label: string;
  value: number | boolean;
  unit: string;
  target: string;
  derivation: string;
  detail: Record<string, unknown>;
}

export interface ScoreTerm {
  name: string; weight: number; value: number; contribution: number;
}

export interface Score { value: number; terms: ScoreTerm[] }

export interface Validation {
  version_id?: string;
  metrics: Metric[];
  scores: Record<string, Score>;
  per_page: {
    page: number; masked_ssim: number; ink_original: number;
    ink_translated: number; ink_delta: number; issues: string[];
  }[];
  issues: Issue[];
  computed_at?: string;
}

export interface Version {
  id: string;
  job_id: string;
  number: number;
  parent_version_id: string | null;
  label: string;
  changed_pages: number[];
  target_lang: string | null;
  document_id: string | null;
  has_pdf: boolean;
  created_at: string;
}

export interface VersionPayload {
  version: Version;
  job: Job;
  document: DocumentInfo;
  segments: Segment[];
  validation: Validation | null;
}

export interface Health {
  status: string;
  provider: string;
  providers_available: string[];
  fonts: { dir: string; faces: number; missing_scripts: string[] };
  ocr: { available: boolean; languages: string[] };
  limits: { max_upload_mb: number; max_pages: number };
  languages: Record<string, { name: string; script: string; rtl: boolean; expansion: number }>;
}

export interface SampleInfo {
  name: string; stem: string; size_bytes: number; page_count: number | null;
  source_lang: string | null; is_scanned: boolean; notes: string;
}

export interface Diff {
  from: string; to: string; from_number: number; to_number: number;
  changed_pages: number[];
  changed_blocks: {
    element_id: string; kind: string; page: number; text_changed?: boolean;
    before?: string; after?: string; rung_before?: number | null;
    rung_after?: number | null; size_before?: number | null;
    size_after?: number | null; bbox_delta_pt?: number;
  }[];
  layout_deltas: { blocks_moved: number; rungs_changed: number };
}
