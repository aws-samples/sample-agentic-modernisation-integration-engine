// Main type definitions for the Code Transformation Engine frontend

// ─── Analysis Request/Response ────────────────────────────────────────────────

export interface AnalysisRequest {
  repo_url: string;
  branch: string;
  pat_token?: string;
}

export interface AnalysisResult {
  analysis_id: string;
  source_type: 'upload' | 'github';
  source_url?: string;
  branch_name?: string;
  filename?: string;
  file_stats: FileTypeStat[];
  folder_structure: FolderNode;
  dependencies: Dependency[];
  dependency_graph: DependencyGraph;
  upgrade_recommendations: UpgradeRecommendation[];
  diagrams: Record<string, { mermaid_code: string }>;
  completed_at: string;
  ai_summary?: string;
  ai_documentation?: string;
  ai_enrichment_status?: 'completed' | 'degraded' | 'failed' | 'skipped';
  ai_enrichment_error?: string;
}

export type AnalysisResponse = AnalysisResult;

export interface AnalysisListItem {
  analysis_id: string;
  source_type: 'upload' | 'github';
  source_url?: string;
  created_at: string;
  status: string;
}

// ─── Progress ─────────────────────────────────────────────────────────────────

export interface ProgressStatus {
  analysis_id: string;
  status: string;
  progress: number;
  current_step: string;
  message: string;
}

// ─── File Statistics ──────────────────────────────────────────────────────────

export interface FileTypeStat {
  extension: string;
  count: number;
  total_lines: number;
  total_size: number;
}

// ─── Folder Structure ─────────────────────────────────────────────────────────

export interface FolderNode {
  name: string;
  type: 'file' | 'directory';
  children?: FolderNode[];
  size?: number;
}

// ─── Dependencies ─────────────────────────────────────────────────────────────

export interface Dependency {
  name: string;
  version: string;
  ecosystem: string;
  source_file: string;
  vulnerabilities?: string[];
}

export interface DependencyGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  metadata?: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}

// ─── Upgrade Recommendations ──────────────────────────────────────────────────

/**
 * Mirrors `services.version_analyzer.UpgradeRecommendation` as persisted by the
 * backend and served by `GET /api/analysis/{id}/upgrade-recommendations`.
 *
 * The identifier is `name` — matching `Dependency.name` — not `package_name`. Stored
 * analyses on disk already carry `name`, so this is the side that had to change.
 */
export interface UpgradeRecommendation {
  name: string;
  /** Version as declared in the manifest. Empty when undeterminable — see `current_version_note`. */
  current_version: string;
  /** Why the current version is unknown. Present only when `current_version` is empty. */
  current_version_note?: string;
  recommended_version: string;
  ecosystem: string;
  reason: string;
}

// ─── Prompt Templates ─────────────────────────────────────────────────────────

export interface PromptTemplate {
  id: string;
  name: string;
  content: string;
  version: string;
  agent: string;
  model: string;
  temperature: number;
}

// ─── Transformation Definitions ───────────────────────────────────────────────

export interface TransformationDefinition {
  id: string;
  name: string;
  description: string;
  type: string;
  definition_path: string;
  published: boolean;
  /**
   * The identifier the ATX CLI accepts for this definition, resolved by the transform
   * agent (`GET /transformations`). Null when the record has no valid identifier.
   * `name` is a display label and must never be sent in its place — AWS-managed labels
   * contain spaces, which the ATX `resource` constraint rejects.
   */
  atx_definition_name?: string | null;
  /**
   * What the transformation migrates from and to (for example "AWS SDK V1" →
   * "AWS SDK V2"). Carried by the AWS-managed catalog the transform agent serves;
   * absent on records created through the backend's custom-definition CRUD.
   */
  source?: string;
  target?: string;
}

// ─── SSE Events ───────────────────────────────────────────────────────────────

export type SSEEvent =
  | { type: 'init'; conversation_id: string; replay?: boolean }
  | { type: 'progress'; message: string; percentage: number }
  | { type: 'content'; text: string }
  | { type: 'info'; text: string }
  | { type: 'tool_use'; tool: string; input: unknown }
  | { type: 'tool_result'; tool: string; output: unknown }
  // ATX agents: `log` is a line of the ATX conversation log (primary console
  // content, streamed live); `output` is a de-noised ATX CLI stdout line.
  | { type: 'log'; data: string; replay?: boolean }
  | { type: 'output'; data: string; replay?: boolean }
  | { type: 'complete'; conversation_id?: string; status?: string; replay?: boolean }
  | { type: 'cancelled'; conversation_id: string }
  | { type: 'error'; message: string; replay?: boolean };

// ─── Auth Config ──────────────────────────────────────────────────────────────

export interface AuthConfig {
  mode: string;
  cognito_user_pool_id?: string;
  cognito_client_id?: string;
  cognito_domain?: string;
  redirect_uri?: string;
}
