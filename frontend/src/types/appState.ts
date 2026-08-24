export interface AppState {
  selectedNavItem: string;
  currentAnalysisId: string | null;
  analysisResults: Record<string, unknown> | null;
  selectedFile: string | null;
  isLoading: boolean;
  analysisCount: number;
}
