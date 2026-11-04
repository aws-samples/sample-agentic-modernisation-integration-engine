// Database Discovery (SQL Server → Aurora MySQL) report types.
// Shapes mirror backend/services/db_discovery/discovery_service.py.

export type Severity = 'blocker' | 'high' | 'medium' | 'low' | 'info';
export type Effort = 'low' | 'medium' | 'high' | 'very_high';

export const SEVERITIES: Severity[] = ['blocker', 'high', 'medium', 'low', 'info'];
export const EFFORTS: Effort[] = ['low', 'medium', 'high', 'very_high'];

export interface DbColumn {
  name: string;
  sql_type: string;
  nullable: boolean;
  identity: boolean;
  computed: boolean;
  persisted: boolean;
  has_default: boolean;
  default_sequence: boolean;
  generated_always: boolean;
  mysql_type: string;
  mapping_note: string;
}

export interface DbObject {
  id: string;
  schema: string;
  name: string;
  type: string;
  subtype: string;
  file: string;
  line: number;
  end_line: number;
  lines_of_code: number;
  parent: string;
  alias_base_type: string;
  primary_key?: string[];
  /** T-SQL text of procedures, functions, views and triggers (empty for other types). */
  source?: string;
  columns: DbColumn[];
  references: string[];
  referenced_by: string[];
  called_from_code: string[];
  ast_parsed: boolean;
  finding_counts: Record<Severity, number>;
  effort_points: number;
  effort: Effort;
}

export interface DbFinding {
  id: string;
  rule_id: string;
  title: string;
  category: string;
  severity: Severity;
  source: 'database' | 'application';
  object_id: string;
  file: string;
  line: number;
  occurrences: number;
  lines: number[];
  snippet: string;
  recommendation: string;
}

export interface TypeMapping {
  sql_server_type: string;
  mysql_type: string;
  note: string;
  column_count: number;
}

export interface EmbeddedSql {
  id: string;
  file: string;
  line: number;
  sql: string;
  interpolated: boolean;
  concatenated: boolean;
  dynamic: boolean;
  rule_ids: string[];
}

export interface ProcedureCall {
  file: string;
  line: number;
  procedure: string;
  object_id: string | null;
  via: string;
  resolved: boolean;
}

export interface ConnectionStringInfo {
  file: string;
  line: number;
  name: string;
  connection_string: string;
  server: string;
  database: string;
  provider: string;
  windows_auth: boolean;
  mars: boolean;
  password_present: boolean;
}

export interface PackageInfo {
  name: string;
  version: string;
  file: string;
  replacement: string;
  source: string;
}

export interface ApplicationScan {
  files: { file: string; technologies: string[]; embedded_sql_count: number; finding_count: number }[];
  embedded_sql: EmbeddedSql[];
  stored_procedure_calls: ProcedureCall[];
  connection_strings: ConnectionStringInfo[];
  packages: PackageInfo[];
  target_frameworks: { file: string; framework: string }[];
  technologies: { name: string; files: number }[];
}

export interface TopRule {
  rule_id: string;
  title: string;
  severity: Severity;
  category: string;
  occurrences: number;
  objects: number;
}

export interface DiscoverySummary {
  files_scanned: { sql: number; application: number; skipped_too_large: string[] };
  object_counts: Record<string, number>;
  finding_counts: Record<Severity, number>;
  finding_counts_by_source: Record<string, number>;
  category_counts: Record<string, number>;
  effort_counts: Record<Effort, number>;
  effort_formula: string;
  objects_needing_manual_work: number;
  top_rules: TopRule[];
  schemas: string[];
  total_sql_lines: number;
  ast_parse: { batches: number; parsed: number };
  logic_distribution: {
    database_modules: number;
    database_module_lines: number;
    embedded_sql_statements: number;
    dynamic_embedded_sql_statements: number;
  };
  application: {
    files_with_findings: number;
    embedded_sql_statements: number;
    stored_procedure_calls: number;
    unresolved_procedure_calls: number;
    procedures_called_from_code: number;
    procedures_not_called_from_code: number;
    connection_strings: number;
    technologies: string[];
  };
}

export interface DiscoveryReport {
  discovery_id: string;
  source_type: string;
  source_url: string;
  branch: string;
  subpath: string;
  target: string;
  completed_at: string;
  summary: DiscoverySummary;
  objects: DbObject[];
  findings: DbFinding[];
  type_mappings: TypeMapping[];
  application: ApplicationScan;
}

export interface DiscoveryListItem {
  discovery_id: string;
  source_type: string;
  source_url: string;
  subpath: string;
  completed_at: string;
  object_counts: Record<string, number>;
  finding_counts: Partial<Record<Severity, number>>;
}

export interface DiscoveryStatus {
  discovery_id: string;
  status: string;
  progress: number;
  current_step: string;
  message: string;
}

// ─── Phase 2: conversion, validation ─────────────────────────────────────────

export type ConversionStatus =
  | 'queued'
  | 'converting'
  | 'converted'
  | 'needs_review'
  | 'failed'
  | 'approved'
  | 'rejected';

export interface JudgeResult {
  scores: Record<string, number>;
  verdict: 'pass' | 'revise';
  issues: string[];
}

export type BehaviourStatus = 'passed' | 'failed' | 'skipped' | 'not_configured' | 'error' | 'not_run';

export interface BehaviourCase {
  name: string;
  params: { name: string; value: string | number | boolean | null }[];
  origin?: 'generated' | 'custom';
}

export interface BehaviourTestResult extends BehaviourCase {
  status: 'match' | 'mismatch' | 'skipped';
  details: string[];
  source_error: string;
  target_error: string;
  result_rows?: { source: number[]; target: number[] };
}

export interface BehaviourResult {
  status: BehaviourStatus;
  reason?: string;
  counts?: Record<string, number>;
  tests?: BehaviourTestResult[];
}

export interface ConversionAttempt {
  n: number;
  sql: string;
  notes: string;
  compile: { ok: boolean; errno: number | null; message: string } | null;
  judge: JudgeResult | null;
  behaviour?: BehaviourResult;
  passed: boolean;
}

export interface ConversionRecord {
  object_id: string;
  object_type: string;
  object_name: string;
  status: ConversionStatus;
  attempts: ConversionAttempt[];
  final_sql: string;
  notes: string;
  reviewer_note: string;
  edited: boolean;
  error: string;
  behaviour_status?: BehaviourStatus;
  test_cases?: BehaviourCase[];
  custom_tests?: BehaviourCase[];
  copy_problems?: { table: string; reason: string }[];
  updated_at: string;
}

export interface SandboxStatus {
  configured: boolean;
  reachable: boolean;
  version: string;
  error?: string;
}

export interface ConnectionStatus {
  engine: string;
  configured: boolean;
  reachable: boolean;
  host?: string;
  port?: number;
  user?: string;
  database?: string;
  sandbox?: boolean;
  version?: string;
  error?: string;
}

export interface ValidationStatus {
  source: ConnectionStatus;
  target: ConnectionStatus;
  copy_enabled: boolean;
}

export interface TableValidation {
  object_id: string;
  table: string;
  columns_compared: number;
  excluded_columns: { column: string; reason: string }[];
  source_rows: number | null;
  target_rows: number | null;
  source_checksum: string;
  target_checksum: string;
  status: 'match' | 'mismatch' | 'not_loaded' | 'missing_in_source' | 'missing_in_target' | 'error';
  error: string;
}

export interface ProcedureValidation {
  procedure: string;
  params: { name: string; value: unknown }[];
  status: 'match' | 'mismatch' | 'error';
  error: string;
  details: string[];
  source_result_sets?: number[];
  target_result_sets?: number[];
}

export interface ValidationRun {
  run_id: string;
  kind: 'validation' | 'copy';
  discovery_id: string;
  status: 'processing' | 'completed' | 'failed';
  progress: number;
  message: string;
  started_at: string;
  completed_at: string;
  summary?: Record<string, number>;
  result?: {
    tables: (TableValidation & { rows?: number })[];
    procedures?: ProcedureValidation[];
    summary?: Record<string, number>;
  };
}

export interface ProcedureTestInput {
  procedure: string;
  params: { name: string; value: string | number | boolean | null }[];
  ordered?: boolean;
  target_call?: string;
}
