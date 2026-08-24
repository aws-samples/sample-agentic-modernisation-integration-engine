// Type definitions for Design Doc Agent

export interface Stage {
  number: number;
  name: string;
  progress_percent: number;
}

export interface DesignJobOutput {
  checklist?: string;
  architecture?: string;
  diagram?: string;
  migration_strategy?: string;
}

export interface DesignJob {
  id: string;
  status: 'PROCESSING' | 'COMPLETED' | 'NEEDS_REVIEW' | 'FAILED';
  progress: number;
  current_stage: number;
  inputs: {
    assessment_report: Record<string, unknown>;
    code_analysis: string;
  };
  outputs: DesignJobOutput;
  versions: Record<string, unknown[]>;
  created_at: string;
  updated_at: string;
}
