import axios, { type AxiosInstance } from 'axios';
import type {
  AnalysisListItem,
  AnalysisResult,
  ProgressStatus,
  FileTypeStat,
  FolderNode,
  Dependency,
  DependencyGraph,
  UpgradeRecommendation,
  TransformationDefinition,
  SSEEvent,
} from '../types';

// ─── Axios Instances ──────────────────────────────────────────────────────────
// All base URLs use relative paths (Vite proxy or Nginx handles routing)

function createInstance(baseURL: string): AxiosInstance {
  const instance = axios.create({ baseURL });

  // JWT request interceptor
  instance.interceptors.request.use((config) => {
    const token = localStorage.getItem('auth_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  // 401 response interceptor
  instance.interceptors.response.use(
    (response) => response,
    (error) => {
      if (axios.isAxiosError(error) && error.response?.status === 401) {
        localStorage.removeItem('auth_token');
        window.location.reload();
      }
      return Promise.reject(error);
    }
  );

  return instance;
}

const backendApi = createInstance('/api');
const atxApi = createInstance('/atx');
const atxTransformApi = createInstance('/atx-transform');
const designDocApi = createInstance('/design-doc');

// ─── SSE Streaming Pattern ────────────────────────────────────────────────────
// Uses native fetch + AbortController + TextDecoder buffer-split-on-newline

function streamSSE(
  url: string,
  onEvent: (event: SSEEvent) => void,
  options?: { method?: string; body?: unknown }
): AbortController {
  // SSRF guard (CWE-918): every caller passes a relative, same-origin API path
  // (e.g. `/api/analysis/${id}/...`). Reject anything that is not a root-relative
  // path so an interpolated value can never redirect the request to an external
  // or protocol-relative host.
  if (!url.startsWith('/') || url.startsWith('//')) {
    throw new Error(`Refusing to stream from non-relative URL: ${url}`);
  }

  const controller = new AbortController();
  const token = localStorage.getItem('auth_token');

  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  if (options?.body) {
    headers['Content-Type'] = 'application/json';
  }

  fetch(url, {
    method: options?.method ?? 'GET',
    headers,
    body: options?.body ? JSON.stringify(options.body) : undefined,
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        onEvent({ type: 'error', message: `HTTP ${response.status}` });
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        onEvent({ type: 'error', message: 'No response body' });
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;

          // Handle SSE format: "data: {...}"
          const dataPrefix = 'data: ';
          const jsonStr = trimmed.startsWith(dataPrefix)
            ? trimmed.slice(dataPrefix.length)
            : trimmed;

          try {
            const parsed = JSON.parse(jsonStr) as SSEEvent;
            onEvent(parsed);
          } catch {
            // Skip non-JSON lines (e.g., SSE comments, event: lines)
          }
        }
      }

      // Process remaining buffer
      if (buffer.trim()) {
        const dataPrefix = 'data: ';
        const jsonStr = buffer.trim().startsWith(dataPrefix)
          ? buffer.trim().slice(dataPrefix.length)
          : buffer.trim();
        try {
          const parsed = JSON.parse(jsonStr) as SSEEvent;
          onEvent(parsed);
        } catch {
          // Ignore trailing incomplete data
        }
      }
    })
    .catch((err: unknown) => {
      if (err instanceof Error && err.name === 'AbortError') return;
      const message = err instanceof Error ? err.message : 'Stream error';
      onEvent({ type: 'error', message });
    });

  return controller;
}

// ─── Backend API Methods ──────────────────────────────────────────────────────

// Analysis CRUD
export async function getAnalyses(): Promise<AnalysisListItem[]> {
  const response = await backendApi.get('/analyses');
  return response.data.analyses ?? [];
}

export async function getAnalysisStatus(id: string): Promise<ProgressStatus> {
  const response = await backendApi.get(`/analysis/${id}/status`);
  return response.data;
}

export async function getAnalysisSummary(id: string): Promise<AnalysisResult> {
  const response = await backendApi.get(`/analysis/${id}/summary`);
  return response.data;
}

export async function getFileStats(id: string): Promise<FileTypeStat[]> {
  const response = await backendApi.get(`/analysis/${id}/file-stats`);
  return response.data.file_stats ?? [];
}

export async function getFolderStructure(id: string): Promise<FolderNode> {
  const response = await backendApi.get(`/analysis/${id}/folder-structure`);
  return response.data.folder_structure ?? { name: 'root', type: 'directory' };
}

export async function getDependencies(id: string): Promise<Dependency[]> {
  const response = await backendApi.get(`/analysis/${id}/dependencies`);
  return response.data.dependencies ?? [];
}

export async function getDependencyGraph(id: string): Promise<DependencyGraph> {
  const response = await backendApi.get(`/analysis/${id}/dependency-graph`);
  return response.data.dependency_graph ?? { nodes: [], edges: [] };
}

export async function getUpgradeRecommendations(id: string): Promise<UpgradeRecommendation[]> {
  const response = await backendApi.get(`/analysis/${id}/upgrade-recommendations`);
  return response.data.upgrade_recommendations ?? [];
}

export async function getDiagrams(id: string): Promise<Record<string, { mermaid_code: string }>> {
  const response = await backendApi.get(`/analysis/${id}/diagrams`);
  return response.data.diagrams ?? {};
}

export async function getMermaid(id: string): Promise<Record<string, string>> {
  const response = await backendApi.get(`/analysis/${id}/mermaid`);
  return response.data;
}

export async function getDocumentation(id: string): Promise<{ documentation: string; ai_enrichment_status: string }> {
  const response = await backendApi.get(`/analysis/${id}/documentation`);
  return response.data;
}

export async function deleteAnalysis(id: string): Promise<void> {
  await backendApi.delete(`/analysis/${id}`);
}

// Analysis initiation
export async function uploadZip(file: File): Promise<{ analysis_id: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await backendApi.post('/analyze/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
}

export async function analyzeGithub(repoUrl: string, branch: string, patToken?: string): Promise<{ analysis_id: string }> {
  const response = await backendApi.post('/analyze/github', {
    repo_url: repoUrl,
    branch,
    pat_token: patToken,
  });
  return response.data;
}

// AI Streaming endpoints
export function streamDocumentation(id: string, onEvent: (event: SSEEvent) => void): AbortController {
  return streamSSE(`/api/analysis/${id}/documentation`, onEvent, { method: 'POST' });
}

export function streamJudge(id: string, onEvent: (event: SSEEvent) => void): AbortController {
  return streamSSE(`/api/analysis/${id}/judge`, onEvent, { method: 'POST' });
}

export function streamFileAnalysis(id: string, filePath: string, onEvent: (event: SSEEvent) => void): AbortController {
  return streamSSE(`/api/analysis/${id}/file-analysis`, onEvent, {
    method: 'POST',
    body: { file_path: filePath },
  });
}

export function streamKiroCli(id: string, onEvent: (event: SSEEvent) => void): AbortController {
  return streamSSE(`/api/analysis/${id}/kiro-cli`, onEvent, { method: 'POST' });
}

// Doc analysis storage
export async function getDocAnalysis(id: string): Promise<unknown> {
  const response = await backendApi.get(`/analysis/${id}/doc-analysis`);
  return response.data;
}

export async function getDocAnalysisRuns(id: string): Promise<unknown[]> {
  const response = await backendApi.get(`/analysis/${id}/doc-analysis/runs`);
  return response.data;
}

export async function getDocAnalysisRun(id: string, timestamp: string): Promise<unknown> {
  const response = await backendApi.get(`/analysis/${id}/doc-analysis/run/${timestamp}`);
  return response.data;
}

export async function deleteDocAnalysis(id: string): Promise<void> {
  await backendApi.delete(`/analysis/${id}/doc-analysis`);
}

export async function downloadKiroSpec(id: string): Promise<Blob> {
  const response = await backendApi.post(`/analysis/${id}/kiro-spec/download`, null, {
    responseType: 'blob',
  });
  return response.data;
}

// Transformation definitions (backend)
export async function getTransformationDefinitions(): Promise<TransformationDefinition[]> {
  const response = await backendApi.get('/transformations/definitions');
  return response.data.definitions ?? [];
}

export async function createTransformationDefinition(def: Omit<TransformationDefinition, 'id'>): Promise<TransformationDefinition> {
  const response = await backendApi.post('/transformations/definitions', def);
  return response.data;
}

export async function updateTransformationDefinition(id: string, def: Partial<TransformationDefinition>): Promise<TransformationDefinition> {
  const response = await backendApi.put(`/transformations/definitions/${id}`, def);
  return response.data;
}

export async function deleteTransformationDefinition(id: string): Promise<void> {
  await backendApi.delete(`/transformations/definitions/${id}`);
}

// Prompt library
export async function getPromptLibrary(): Promise<unknown> {
  const response = await backendApi.get('/prompts');
  return response.data;
}

export async function getPrompt(id: string): Promise<unknown> {
  const response = await backendApi.get(`/prompts/${id}`);
  return response.data;
}

export async function updatePrompt(id: string, data: unknown): Promise<unknown> {
  const response = await backendApi.put(`/prompts/${id}`, data);
  return response.data;
}

// Storage stats
export async function getStorageStats(): Promise<unknown> {
  const response = await backendApi.get('/storage/stats');
  return response.data;
}

// ─── ATX Analysis Agent Methods ───────────────────────────────────────────────

export interface AtxConversation {
  conversation_id: string;
  status: string;
  created_at: string;
}

export function startAtxAnalysis(
  repositoryUrl: string,
  analysisType: string,
  onEvent: (event: SSEEvent) => void
): AbortController {
  return streamSSE('/atx/analyze', onEvent, {
    method: 'POST',
    body: { repository_url: repositoryUrl, analysis_type: analysisType },
  });
}

/**
 * Reconnect to an existing ATX conversation.
 *
 * Replays every event the agent already emitted (flagged `replay: true`), then
 * continues live if the analysis is still running. Used on mount so a page
 * refresh restores a running analysis instead of an empty console.
 */
export function streamAtxConversation(
  conversationId: string,
  onEvent: (event: SSEEvent) => void
): AbortController {
  return streamSSE(`/atx/conversations/${conversationId}/stream`, onEvent);
}

export async function cancelAtxAnalysis(conversationId: string): Promise<void> {
  await atxApi.post(`/cancel/${conversationId}`);
}

export async function getAtxConversations(): Promise<AtxConversation[]> {
  const response = await atxApi.get('/conversations');
  return response.data.conversations ?? [];
}

export interface AtxDoc {
  name: string;
  /** Path relative to the conversation's docs/ directory. */
  path: string;
  /** Path relative to the storage root — pass straight to `getAtxFileContent`. */
  storage_path: string;
  size: number;
}

export interface AtxDocsResponse {
  docs: AtxDoc[];
  /** Conversation status, so an empty list can be explained rather than guessed. */
  status: string;
}

export async function getAtxConversationDocs(conversationId: string): Promise<AtxDocsResponse> {
  const response = await atxApi.get(`/conversations/${conversationId}/docs`);
  return { docs: response.data?.docs ?? [], status: response.data?.status ?? 'unknown' };
}

/**
 * Read one collected document. Goes through the agent's `/file` endpoint, which
 * already enforces the storage-root path check and a 10MB cap.
 */
export async function getAtxFileContent(path: string): Promise<string> {
  const response = await atxApi.get('/file', { params: { path } });
  return response.data?.content ?? '';
}

export async function getAtxConversationLogs(conversationId: string): Promise<unknown> {
  const response = await atxApi.get(`/conversations/${conversationId}/logs`);
  return response.data;
}

export async function getAtxBrowse(path?: string): Promise<unknown> {
  const response = await atxApi.get('/browse', { params: { path } });
  return response.data;
}

export async function getAtxFile(path: string): Promise<unknown> {
  const response = await atxApi.get('/file', { params: { path } });
  return response.data;
}

export async function getAtxAnalysisDefinitions(): Promise<unknown[]> {
  const response = await atxApi.get('/analysis-definitions');
  return response.data;
}

// ─── ATX Transform Agent Methods ──────────────────────────────────────────────

export interface TransformRecord {
  repo_id: string;
  status: string;
  created_at: string;
  /**
   * Null for a record the agent recovered from storage: the URL was only ever in the
   * original request body, so it is reported as unknown rather than guessed. The
   * history list already falls back to the repo_id for its label.
   */
  repo_url: string | null;
}

export async function startTransformation(
  repoUrl: string,
  branch: string,
  transformationType: string,
  configuration?: string
): Promise<{ repo_id: string; status: string }> {
  const response = await atxTransformApi.post('/transform', {
    repo_url: repoUrl,
    branch,
    transformation_type: transformationType,
    configuration,
  });
  return response.data;
}

export async function getTransformationHistory(): Promise<TransformRecord[]> {
  const response = await atxTransformApi.get('/transformation-history');
  return response.data.records ?? [];
}

export async function getTransformations(): Promise<TransformationDefinition[]> {
  const response = await atxTransformApi.get('/transformations');
  return response.data.definitions ?? [];
}

export function streamTransformConversation(
  repoId: string,
  onEvent: (event: SSEEvent) => void
): AbortController {
  return streamSSE(`/atx-transform/conversations/${repoId}/stream`, onEvent);
}

export async function getDiff(repoId: string): Promise<unknown> {
  const response = await atxTransformApi.get(`/diff/${repoId}`);
  return response.data;
}

export async function getDiffSummary(repoId: string): Promise<unknown> {
  const response = await atxTransformApi.get(`/diff-summary/${repoId}`);
  return response.data;
}

/*
 * `createPR` / `getPRPreview` wrappers for the transform agent's
 * `POST /create-file-pr/{repo_id}` and `GET /pr-preview/{repo_id}` used to live here.
 * The Create PR affordance was removed from the transform results page and nothing
 * else called them, so keeping the wrappers would have left client code with no
 * caller. The agent endpoints are unchanged.
 */

export async function getTransformBranches(): Promise<string[]> {
  const response = await atxTransformApi.get('/branches');
  return response.data;
}

/**
 * Download the whole transformed tree as a zip.
 *
 * Fetched as a blob rather than linked directly because the agent is reached through
 * the JWT-bearing axios instance — a bare `<a href>` would carry no Authorization
 * header. The agent streams the archive, so this holds one response in memory only
 * for as long as the browser needs to hand it to the download manager.
 *
 * Errors arrive as JSON (413 over-cap, 404 missing tree) inside an error blob, so the
 * detail is extracted rather than surfaced as an opaque "Request failed".
 */
export async function downloadTransformedTree(repoId: string): Promise<void> {
  try {
    const response = await atxTransformApi.get(`/download/${repoId}`, { responseType: 'blob' });
    const url = URL.createObjectURL(response.data as Blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `transformed-${repoId}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    throw new Error(await extractBlobErrorDetail(err));
  }
}

async function extractBlobErrorDetail(err: unknown): Promise<string> {
  if (axios.isAxiosError(err) && err.response?.data instanceof Blob) {
    try {
      const parsed = JSON.parse(await err.response.data.text()) as { detail?: string };
      if (parsed.detail) return parsed.detail;
    } catch {
      // Not JSON — fall through to the generic message.
    }
  }
  return err instanceof Error ? err.message : 'Download failed';
}

// ─── Design Doc Agent Methods ─────────────────────────────────────────────────

export async function createDesignJob(data: unknown): Promise<unknown> {
  const response = await designDocApi.post('/api/design-jobs', data);
  return response.data;
}

export async function getDesignJobs(): Promise<unknown[]> {
  const response = await designDocApi.get('/api/design-jobs');
  return response.data;
}

export async function getDesignJob(id: string): Promise<unknown> {
  const response = await designDocApi.get(`/api/design-jobs/${id}`);
  return response.data;
}

export async function getDesignJobStatus(id: string): Promise<unknown> {
  const response = await designDocApi.get(`/api/design-jobs/${id}/status`);
  return response.data;
}

export function streamDesignJob(id: string, onEvent: (event: SSEEvent) => void): AbortController {
  return streamSSE(`/design-doc/api/design-jobs/${id}/stream`, onEvent);
}

// ─── Health Check ─────────────────────────────────────────────────────────────

export async function getHealthStatus(): Promise<{ status: string }> {
  const response = await backendApi.get('/health');
  return response.data;
}
