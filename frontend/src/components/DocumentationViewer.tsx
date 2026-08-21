import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import {
  Box,
  Tabs,
  Tab,
  Typography,
  Button,
  Collapse,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Paper,
  CircularProgress,
  List,
  ListItem,
  ListItemText,
  Alert,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import DownloadIcon from '@mui/icons-material/Download';
import RefreshIcon from '@mui/icons-material/Refresh';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  streamDocumentation,
  getDocAnalysisRuns,
  getDocAnalysisRun,
  downloadKiroSpec,
} from '../services/api';
import type { SSEEvent } from '../types';
import { useMarkdownComponents } from '../utils/markdownComponents';

interface DocumentationViewerProps {
  analysisId: string;
}

interface RunEntry {
  timestamp: string;
  [key: string]: unknown;
}

export function DocumentationViewer({ analysisId }: DocumentationViewerProps) {
  const [activeTab, setActiveTab] = useState(0);
  const [generatedContent, setGeneratedContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [thinkingContent, setThinkingContent] = useState<string[]>([]);
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [feedbackDialogOpen, setFeedbackDialogOpen] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [selectedRun, setSelectedRun] = useState<unknown>(null);
  const [runsLoading, setRunsLoading] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const handleSSEEvent = useCallback((event: SSEEvent) => {
    switch (event.type) {
      case 'content':
        setGeneratedContent((prev) => prev + event.text);
        break;
      case 'tool_use':
      case 'tool_result':
        setThinkingContent((prev) => [
          ...prev,
          `[${event.type}] ${event.tool}: ${JSON.stringify(event.type === 'tool_use' ? event.input : event.output).slice(0, 200)}`,
        ]);
        break;
      case 'complete':
        setIsStreaming(false);
        break;
      case 'error':
        setIsStreaming(false);
        break;
      default:
        break;
    }
  }, []);

  const startStreaming = useCallback(
    (judgeFeedback?: string) => {
      if (controllerRef.current) {
        controllerRef.current.abort();
      }
      setGeneratedContent('');
      setThinkingContent([]);
      setIsStreaming(true);

      const url = judgeFeedback
        ? `/api/analysis/${analysisId}/documentation`
        : `/api/analysis/${analysisId}/documentation`;

      const body = judgeFeedback ? { judge_feedback: judgeFeedback } : undefined;

      const controller = streamDocumentation(analysisId, handleSSEEvent);
      // If we need to pass feedback, abort and re-create with custom body
      if (judgeFeedback) {
        controller.abort();
        const token = localStorage.getItem('auth_token');
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const newController = new AbortController();
        controllerRef.current = newController;

        // SSRF guard (CWE-918): url is a root-relative, same-origin API path.
        // Reject anything else so an interpolated id cannot redirect the request
        // to an external or protocol-relative host.
        if (!url.startsWith('/') || url.startsWith('//')) {
          throw new Error(`Refusing to fetch from non-relative URL: ${url}`);
        }

        fetch(url, {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
          signal: newController.signal,
        })
          .then(async (response) => {
            if (!response.ok) {
              handleSSEEvent({ type: 'error', message: `HTTP ${response.status}` });
              return;
            }
            const reader = response.body?.getReader();
            if (!reader) return;
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
                const dataPrefix = 'data: ';
                const jsonStr = trimmed.startsWith(dataPrefix) ? trimmed.slice(dataPrefix.length) : trimmed;
                try {
                  const parsed = JSON.parse(jsonStr) as SSEEvent;
                  handleSSEEvent(parsed);
                } catch {
                  // skip
                }
              }
            }
          })
          .catch((err: unknown) => {
            if (err instanceof Error && err.name === 'AbortError') return;
            handleSSEEvent({ type: 'error', message: err instanceof Error ? err.message : 'Stream error' });
          });
      } else {
        controllerRef.current = controller;
      }
    },
    [analysisId, handleSSEEvent]
  );

  const handleRegenerateSubmit = useCallback(() => {
    setFeedbackDialogOpen(false);
    startStreaming(feedback);
    setFeedback('');
  }, [feedback, startStreaming]);

  const handleDownload = useCallback(() => {
    if (generatedContent) {
      const blob = new Blob([generatedContent], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${analysisId}-documentation.md`;
      a.click();
      URL.revokeObjectURL(url);
    }
  }, [generatedContent, analysisId]);

  const handleKiroSpecDownload = useCallback(async () => {
    try {
      const blob = await downloadKiroSpec(analysisId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${analysisId}-kiro-spec.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // Silently handle download errors
    }
  }, [analysisId]);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const data = await getDocAnalysisRuns(analysisId);
      setRuns(Array.isArray(data) ? (data as RunEntry[]) : []);
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, [analysisId]);

  const loadRunDetail = useCallback(
    async (timestamp: string) => {
      try {
        const data = await getDocAnalysisRun(analysisId, timestamp);
        setSelectedRun(data);
      } catch {
        setSelectedRun(null);
      }
    },
    [analysisId]
  );

  useEffect(() => {
    if (activeTab === 2) {
      loadRuns();
    }
  }, [activeTab, loadRuns]);

  // A run's stored documentation, or its raw record when there is no documentation field.
  const selectedRunMarkdown = useMemo(() => {
    if (typeof selectedRun === 'object' && selectedRun !== null && 'documentation' in selectedRun) {
      return String((selectedRun as Record<string, unknown>).documentation);
    }
    return JSON.stringify(selectedRun, null, 2);
  }, [selectedRun]);

  // Anchor and link handling is shared with the other generated-markdown surfaces — see
  // `utils/markdownComponents`. Each source gets its own map because the set of heading
  // anchors a document offers is a property of that document.
  const generatedComponents = useMarkdownComponents({ markdown: generatedContent });
  const historyComponents = useMarkdownComponents({ markdown: selectedRunMarkdown });

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h5">Documentation</Typography>
        <Box>
          {generatedContent && (
            <Button startIcon={<DownloadIcon />} onClick={handleDownload} sx={{ mr: 1 }}>
              Download
            </Button>
          )}
          <Button
            startIcon={<RefreshIcon />}
            variant="contained"
            color="secondary"
            onClick={() => setFeedbackDialogOpen(true)}
            disabled={isStreaming}
          >
            Regenerate
          </Button>
        </Box>
      </Box>

      <Tabs value={activeTab} onChange={(_, v) => setActiveTab(v)} sx={{ mb: 2 }}>
        <Tab label="Generated" />
        <Tab label="Kiro Spec" />
        <Tab label="History" />
      </Tabs>

      {/* Generated Tab */}
      {activeTab === 0 && (
        <Box>
          {!generatedContent && !isStreaming && (
            <Box sx={{ textAlign: 'center', py: 4 }}>
              <Typography color="text.secondary" sx={{ mb: 2 }}>
                No documentation generated yet.
              </Typography>
              <Button variant="contained" onClick={() => startStreaming()} disabled={isStreaming}>
                Generate Documentation
              </Button>
            </Box>
          )}

          {isStreaming && !generatedContent && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
              <CircularProgress size={20} />
              <Typography color="text.secondary">Generating documentation...</Typography>
            </Box>
          )}

          {thinkingContent.length > 0 && (
            <Paper sx={{ mb: 2, p: 1 }}>
              <Box
                sx={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}
                onClick={() => setThinkingOpen(!thinkingOpen)}
              >
                <IconButton size="small">
                  {thinkingOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                </IconButton>
                <Typography variant="body2" color="text.secondary">
                  Agent Thinking ({thinkingContent.length} steps)
                </Typography>
              </Box>
              <Collapse in={thinkingOpen}>
                <Box sx={{ p: 1, maxHeight: 200, overflow: 'auto', fontSize: '0.75rem', fontFamily: 'monospace' }}>
                  {thinkingContent.map((line, i) => (
                    <Typography key={i} variant="caption" component="div" sx={{ fontFamily: 'monospace' }}>
                      {line}
                    </Typography>
                  ))}
                </Box>
              </Collapse>
            </Paper>
          )}

          {generatedContent && (
            <Paper sx={{ p: 3 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={generatedComponents}>
                {generatedContent}
              </ReactMarkdown>
            </Paper>
          )}
        </Box>
      )}

      {/* Kiro Spec Tab */}
      {activeTab === 1 && (
        <Box sx={{ textAlign: 'center', py: 4 }}>
          <Typography color="text.secondary" sx={{ mb: 2 }}>
            Download the generated Kiro specification for this analysis.
          </Typography>
          <Button variant="contained" startIcon={<DownloadIcon />} onClick={handleKiroSpecDownload}>
            Download Kiro Spec
          </Button>
        </Box>
      )}

      {/* History Tab */}
      {activeTab === 2 && (
        <Box>
          {runsLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={24} />
            </Box>
          ) : runs.length === 0 ? (
            <Alert severity="info">No previous documentation runs found.</Alert>
          ) : (
            <Box sx={{ display: 'flex', gap: 2 }}>
              <List sx={{ width: 300, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
                {runs.map((run) => (
                  <ListItem
                    key={run.timestamp}
                    component="div"
                    onClick={() => loadRunDetail(run.timestamp)}
                    sx={{ cursor: 'pointer', '&:hover': { bgcolor: 'action.hover' } }}
                  >
                    <ListItemText
                      primary={new Date(run.timestamp).toLocaleString()}
                      secondary={String(run.status ?? 'completed')}
                    />
                  </ListItem>
                ))}
              </List>
              {selectedRun !== null ? (
                <Paper sx={{ flex: 1, p: 2, overflow: 'auto', maxHeight: 500 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={historyComponents}>
                    {selectedRunMarkdown}
                  </ReactMarkdown>
                </Paper>
              ) : null}
            </Box>
          )}
        </Box>
      )}

      {/* Regenerate with feedback dialog */}
      <Dialog open={feedbackDialogOpen} onClose={() => setFeedbackDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Regenerate Documentation</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Provide feedback to guide the regeneration. Leave empty to regenerate without feedback.
          </Typography>
          <TextField
            label="Feedback (optional)"
            multiline
            rows={4}
            fullWidth
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="e.g., Focus more on architecture patterns, add more code examples..."
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setFeedbackDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleRegenerateSubmit}>
            Regenerate
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
