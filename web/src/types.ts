export type NodeStatus = "pending" | "running" | "done" | "error" | "skipped";
export type RunMode = "fast" | "quality";

export interface AgentNode {
  key: string;
  name: string;
  role: string;
  mark: string;
}

export interface TimelineEvent {
  id: number;
  type: string;
  label: string;
  detail: string;
  time: string;
}

export interface AuditIssue {
  level?: string;
  category?: string;
  position?: string;
  suggestion?: string;
}

export interface AuditResult {
  passed?: boolean;
  score?: number;
  summary?: string;
  degrade_mode?: boolean;
  issues?: Array<AuditIssue | string>;
}

export interface RunMetrics {
  elapsed_ms?: number;
  total_tokens?: number;
  usage_estimated?: boolean;
  llm_calls?: number;
  llm_failed_calls?: number;
  stub_calls?: number;
  retrieved_count?: number;
  node_errors?: unknown[];
}

export interface RevisionRecord {
  iteration?: number;
  before_score?: number;
  after_score?: number | null;
  issues?: string[];
  unified_diff?: string;
}

export interface ReferenceSource {
  material_id: string;
  title: string;
  category: string;
  score: number;
  source: "user_memory" | "public_knowledge" | string;
  source_path?: string;
  owner_user_id?: string | null;
}

export interface GenerationResult {
  success?: boolean;
  run_mode?: RunMode;
  task_type?: string;
  content?: string;
  error?: string;
  elapsed_ms?: number;
  iteration_count?: number;
  session_id?: string;
  audit_result?: AuditResult | null;
  metrics?: RunMetrics;
  revisions?: RevisionRecord[];
  reference_sources?: ReferenceSource[];
}

export interface HealthState {
  online: boolean;
  checking: boolean;
  llmConfigured: boolean;
  model: string;
  embeddingDegraded: boolean;
  embeddingHint: string;
}

export interface MemoryItem {
  memory_id: string;
  title?: string;
  question: string;
  answer?: string;
  answer_preview?: string;
  session_id?: string;
  created_ts?: number;
  updated_ts?: number;
}

export interface ReviewScores {
  hook: number | null;
  pacing: number | null;
  character_consistency: number | null;
  shootability: number | null;
  compliance: number | null;
  notes: string;
}
