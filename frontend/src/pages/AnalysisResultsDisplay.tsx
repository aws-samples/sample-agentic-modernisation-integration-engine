import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import {
  Box,
  Tabs,
  Tab,
  Typography,
  CircularProgress,
  Card,
  CardContent,
  Grid,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  Alert,
} from '@mui/material';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  getAnalysisSummary,
  getFileStats,
  getFolderStructure,
  getDependencies,
  getDependencyGraph,
  getUpgradeRecommendations,
  getDiagrams,
  getDocumentation,
} from '../services/api';
import type {
  AnalysisResult,
  FileTypeStat,
  FolderNode,
  Dependency,
  UpgradeRecommendation,
} from '../types';
import type { DependencyGraph as DependencyGraphData } from '../types';
import { DependencyGraph as DependencyGraphViz } from '../components/DependencyGraph';
import { DiagramViewer } from '../components/DiagramViewer';
import { FileStatsChart } from '../components/FileStatsChart';
import { FolderTree } from '../components/FolderTree';
import { DependencyViewer } from '../components/DependencyViewer';
import { useMarkdownComponents } from '../utils/markdownComponents';

// ─── Markdown rendering (BC-6) ────────────────────────────────────────────────
//
// Anchor and link handling is shared with the ATX documentation tab — see
// `utils/markdownComponents`. These tabs render a single AI-generated document with no
// surrounding collection, so no `navigation` is supplied: a relative link here has nothing
// to resolve against and is reported unfollowable rather than opened in a tab the SPA
// cannot serve.

// ─── Tab Panel ────────────────────────────────────────────────────────────────

interface TabPanelProps {
  children: React.ReactNode;
  value: number;
  index: number;
}

function TabPanel({ children, value, index }: TabPanelProps) {
  if (value !== index) return null;
  return <Box sx={{ py: 2 }}>{children}</Box>;
}

// ─── AI enrichment status ─────────────────────────────────────────────────────

/**
 * Surfaces the AI enrichment outcome on the Summary tab.
 *
 * `failed` and `skipped` mean different things and need different responses:
 * a failure is an actionable error (timeout, denied model, expired credential)
 * and the recorded cause is shown, while a skip means the AI step was not
 * attempted. An unrecognised status is reported as unknown rather than being
 * silently swallowed, so a new status can never render as a clean success.
 */
export function EnrichmentStatusAlert({
  status,
  error,
}: {
  status?: AnalysisResult['ai_enrichment_status'];
  error?: string;
}) {
  if (!status || status === 'completed') return null;

  if (status === 'degraded') {
    return (
      <Alert severity="warning" sx={{ mb: 2 }}>
        The AI narrative below was generated without the analysis context, so it does not
        describe the analysed codebase.{error ? ` Cause: ${error}` : ''}
      </Alert>
    );
  }

  if (status === 'failed') {
    return (
      <Alert severity="error" sx={{ mb: 2 }}>
        AI enrichment failed for this analysis. All code analysis results below are
        complete and unaffected.{error ? ` Cause: ${error}` : ''}
      </Alert>
    );
  }

  if (status === 'skipped') {
    return (
      <Alert severity="info" sx={{ mb: 2 }}>
        AI enrichment was not run for this analysis. All code analysis results below are
        complete and unaffected.{error ? ` Reason: ${error}` : ''}
      </Alert>
    );
  }

  return (
    <Alert severity="warning" sx={{ mb: 2 }}>
      AI enrichment reported an unrecognised status ({String(status)}), so the AI content
      below may be unreliable.{error ? ` Detail: ${error}` : ''}
    </Alert>
  );
}

// ─── Summary Tab ──────────────────────────────────────────────────────────────

function SummaryTab({ data }: { data: AnalysisResult | null }) {
  const markdownComponents = useMarkdownComponents({ markdown: data?.ai_summary ?? '' });

  if (!data) return <Typography color="text.secondary">No summary available.</Typography>;

  const fileCount = Array.isArray(data.file_stats) ? data.file_stats.reduce((sum, f) => sum + f.count, 0) : 0;
  const lineCount = Array.isArray(data.file_stats) ? data.file_stats.reduce((sum, f) => sum + f.total_lines, 0) : 0;
  const langCount = Array.isArray(data.file_stats) ? data.file_stats.length : 0;
  const depCount = Array.isArray(data.dependencies) ? data.dependencies.length : 0;

  return (
    <Box>
      <EnrichmentStatusAlert
        status={data.ai_enrichment_status}
        error={data.ai_enrichment_error}
      />

      {data.ai_summary && (
        <Box sx={{ mb: 3 }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {data.ai_summary}
          </ReactMarkdown>
        </Box>
      )}

      <Grid container spacing={2}>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h4" color="secondary.main">{fileCount.toLocaleString()}</Typography>
              <Typography variant="body2" color="text.secondary">Total Files</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h4" color="secondary.main">{lineCount.toLocaleString()}</Typography>
              <Typography variant="body2" color="text.secondary">Total Lines</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h4" color="secondary.main">{langCount}</Typography>
              <Typography variant="body2" color="text.secondary">Languages</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: 'center' }}>
              <Typography variant="h4" color="secondary.main">{depCount}</Typography>
              <Typography variant="body2" color="text.secondary">Dependencies</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Box sx={{ mt: 3 }}>
        <Typography variant="body2" color="text.secondary">
          Analysis ID: <code>{data.analysis_id}</code> | Source: {data.source_type}
          {data.source_url ? ` | ${data.source_url}` : ''}
          {data.completed_at ? ` | Completed: ${new Date(data.completed_at).toLocaleString()}` : ''}
        </Typography>
      </Box>
    </Box>
  );
}

// ─── Files Tab ────────────────────────────────────────────────────────────────

function FilesTab({ data }: { data: FileTypeStat[] | null }) {
  if (!Array.isArray(data)) return <Typography color="text.secondary">No file statistics available.</Typography>;
  if (data.length === 0) return <Typography color="text.secondary">No files found.</Typography>;

  return (
    <Box>
      <FileStatsChart data={data} />
      <TableContainer component={Paper} sx={{ mt: 2, maxHeight: 400 }}>
        <Table stickyHeader size="small">
          <TableHead>
            <TableRow>
              <TableCell>Extension</TableCell>
              <TableCell align="right">Count</TableCell>
              <TableCell align="right">Total Lines</TableCell>
              <TableCell align="right">Total Size (KB)</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.map((stat) => (
              <TableRow key={stat.extension}>
                <TableCell><Chip label={stat.extension || 'unknown'} size="small" variant="outlined" /></TableCell>
                <TableCell align="right">{stat.count}</TableCell>
                <TableCell align="right">{stat.total_lines.toLocaleString()}</TableCell>
                <TableCell align="right">{(stat.total_size / 1024).toFixed(1)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}

// ─── Folders Tab ──────────────────────────────────────────────────────────────

function FoldersTab({ data }: { data: FolderNode | null }) {
  if (!data?.name) return <Typography color="text.secondary">No folder structure available.</Typography>;

  return <FolderTree data={data} />;
}

// ─── Dependencies Tab ─────────────────────────────────────────────────────────

function DependenciesTab({ data }: { data: Dependency[] | null }) {
  if (!Array.isArray(data)) return <Typography color="text.secondary">No dependencies available.</Typography>;
  if (data.length === 0) return <Typography color="text.secondary">No dependencies found.</Typography>;

  return <DependencyViewer data={data} />;
}

// ─── Dep Graph Tab ────────────────────────────────────────────────────────────

function DepGraphTab({ data }: { data: DependencyGraphData | null }) {
  if (!data || !Array.isArray(data.nodes) || data.nodes.length === 0) {
    return <Typography color="text.secondary">No dependency graph available.</Typography>;
  }

  // BC-31: backend may serve relationships as `links` instead of `edges`
  const edges = data.edges ?? (data as unknown as { links?: typeof data.edges }).links ?? [];

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {data.nodes.length} nodes, {edges.length} edges
      </Typography>
      <DependencyGraphViz nodes={data.nodes} edges={edges} />
    </Box>
  );
}

// ─── Upgrades Tab ─────────────────────────────────────────────────────────────

export function UpgradesTab({ data }: { data: UpgradeRecommendation[] | null }) {
  // Three outcomes a reader must be able to tell apart: the data never arrived,
  // the analysis ran and found nothing to upgrade, and the analysis found rows.
  if (!Array.isArray(data)) {
    return <Typography color="text.secondary">Upgrade recommendations could not be loaded for this analysis.</Typography>;
  }
  if (data.length === 0) {
    return (
      <Typography color="text.secondary">
        No upgrades recommended — every declared dependency is current against the known
        advisories and modernization rules.
      </Typography>
    );
  }

  return (
    <TableContainer component={Paper} sx={{ maxHeight: 400 }}>
      <Table stickyHeader size="small">
        <TableHead>
          <TableRow>
            <TableCell>Package</TableCell>
            <TableCell>Ecosystem</TableCell>
            <TableCell>Current Version</TableCell>
            <TableCell>Recommended Version</TableCell>
            <TableCell>Reason</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {data.map((rec, i) => (
            <TableRow key={`${rec.name}-${i}`}>
              <TableCell>{rec.name}</TableCell>
              <TableCell>
                {rec.ecosystem ? <Chip label={rec.ecosystem} size="small" variant="outlined" /> : null}
              </TableCell>
              <TableCell sx={{ fontFamily: rec.current_version ? 'monospace' : undefined }}>
                {rec.current_version || (
                  // A blank cell meaning "we don't know" is indistinguishable from a bug.
                  <Typography component="span" variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
                    {rec.current_version_note || 'version not declared'}
                  </Typography>
                )}
              </TableCell>
              <TableCell sx={{ fontFamily: 'monospace', color: 'success.main' }}>{rec.recommended_version}</TableCell>
              <TableCell>{rec.reason}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

// ─── Diagrams Tab ─────────────────────────────────────────────────────────────

// Backend serves {key: {mermaid_code: "..."}}; DiagramViewer wants Record<string, string>.
function toMermaidSourceMap(
  raw: Record<string, { mermaid_code: string }> | null
): Record<string, string> {
  if (!raw || typeof raw !== 'object') return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === 'string') {
      out[key] = value;
    } else if (value && typeof value === 'object' && typeof value.mermaid_code === 'string') {
      out[key] = value.mermaid_code;
    }
  }
  return out;
}

function DiagramsTab({ data }: { data: Record<string, { mermaid_code: string }> | null }) {
  const diagrams = toMermaidSourceMap(data);

  if (Object.keys(diagrams).length === 0) {
    return <Typography color="text.secondary">No diagrams available.</Typography>;
  }

  return <DiagramViewer diagrams={diagrams} />;
}

// ─── Documentation Tab ────────────────────────────────────────────────────────

function DocumentationTab({ analysisId }: { analysisId: string }) {
  const [docData, setDocData] = useState<{ documentation: string; ai_enrichment_status: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    getDocumentation(analysisId)
      .then((data) => {
        if (mounted) setDocData(data);
      })
      .catch((err: unknown) => {
        if (mounted) setError(err instanceof Error ? err.message : 'Failed to load documentation');
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => { mounted = false; };
  }, [analysisId]);

  const markdownComponents = useMarkdownComponents({ markdown: docData?.documentation ?? '' });

  if (loading) return <CircularProgress size={24} />;
  if (error) return <Alert severity="error">{error}</Alert>;

  if (!docData || !docData.documentation) {
    if (docData?.ai_enrichment_status === 'skipped') {
      return (
        <Alert severity="info">
          AI documentation generation was skipped (Bedrock unavailable).
        </Alert>
      );
    }
    // A failure is not a no-op: saying "not available yet" hides a real error
    // that an operator can act on. See the Summary tab for the recorded cause.
    if (docData?.ai_enrichment_status === 'failed') {
      return (
        <Alert severity="error">
          AI documentation generation failed, so no documentation was stored for this
          analysis. The Summary tab shows the recorded cause. Re-run AI enrichment once
          it is resolved.
        </Alert>
      );
    }
    return <Typography color="text.secondary">No AI documentation available yet.</Typography>;
  }

  return (
    <Box>
      {docData.ai_enrichment_status === 'degraded' && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          This documentation was generated without the analysis context, so it does not
          describe the analysed codebase. Re-run AI enrichment to get grounded documentation.
        </Alert>
      )}
      {docData.ai_enrichment_status === 'failed' && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          AI enrichment failed for this analysis, so this documentation may be incomplete.
          The Summary tab shows the recorded cause.
        </Alert>
      )}
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {docData.documentation}
      </ReactMarkdown>
    </Box>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function AnalysisResultsDisplay() {
  const { id } = useParams<{ id: string }>();
  const [tabIndex, setTabIndex] = useState(0);
  const [summary, setSummary] = useState<AnalysisResult | null>(null);
  const [fileStats, setFileStats] = useState<FileTypeStat[] | null>(null);
  const [folderStructure, setFolderStructure] = useState<FolderNode | null>(null);
  const [dependencies, setDependencies] = useState<Dependency[] | null>(null);
  const [depGraph, setDepGraph] = useState<DependencyGraphData | null>(null);
  const [upgrades, setUpgrades] = useState<UpgradeRecommendation[] | null>(null);
  const [diagrams, setDiagrams] = useState<Record<string, { mermaid_code: string }> | null>(null);
  const [loading, setLoading] = useState(false);

  const analysisId = id ?? '';

  const loadTab = useCallback(async (index: number) => {
    if (!analysisId) return;
    setLoading(true);
    try {
      switch (index) {
        case 0:
          if (!summary) {
            const data = await getAnalysisSummary(analysisId);
            setSummary(data);
          }
          break;
        case 1:
          if (!fileStats) {
            const data = await getFileStats(analysisId);
            setFileStats(data);
          }
          break;
        case 2:
          if (!folderStructure) {
            const data = await getFolderStructure(analysisId);
            setFolderStructure(data);
          }
          break;
        case 3:
          if (!dependencies) {
            const data = await getDependencies(analysisId);
            setDependencies(data);
          }
          break;
        case 4:
          if (!depGraph) {
            const data = await getDependencyGraph(analysisId);
            setDepGraph(data);
          }
          break;
        case 5:
          if (!upgrades) {
            const data = await getUpgradeRecommendations(analysisId);
            setUpgrades(data);
          }
          break;
        case 6:
          if (!diagrams) {
            const data = await getDiagrams(analysisId);
            setDiagrams(data);
          }
          break;
        // Tab 7 (Documentation) loads internally
      }
    } catch {
      // Gracefully handle load errors — tabs show fallback content
    } finally {
      setLoading(false);
    }
  }, [analysisId, summary, fileStats, folderStructure, dependencies, depGraph, upgrades, diagrams]);

  useEffect(() => {
    loadTab(tabIndex);
  }, [tabIndex, loadTab]);

  // Load summary on mount
  useEffect(() => {
    loadTab(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisId]);

  if (!analysisId) {
    return <Typography color="text.secondary">No analysis selected.</Typography>;
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Analysis Results
      </Typography>

      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
        <Tabs
          value={tabIndex}
          onChange={(_, v: number) => setTabIndex(v)}
          variant="scrollable"
          scrollButtons="auto"
        >
          <Tab label="Summary" />
          <Tab label="Files" />
          <Tab label="Folders" />
          <Tab label="Dependencies" />
          <Tab label="Dep Graph" />
          <Tab label="Upgrades" />
          <Tab label="Diagrams" />
          <Tab label="Documentation" />
        </Tabs>
      </Box>

      {loading && tabIndex !== 7 && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress size={24} />
        </Box>
      )}

      <TabPanel value={tabIndex} index={0}>
        <SummaryTab data={summary} />
      </TabPanel>
      <TabPanel value={tabIndex} index={1}>
        <FilesTab data={fileStats} />
      </TabPanel>
      <TabPanel value={tabIndex} index={2}>
        <FoldersTab data={folderStructure} />
      </TabPanel>
      <TabPanel value={tabIndex} index={3}>
        <DependenciesTab data={dependencies} />
      </TabPanel>
      <TabPanel value={tabIndex} index={4}>
        <DepGraphTab data={depGraph} />
      </TabPanel>
      <TabPanel value={tabIndex} index={5}>
        <UpgradesTab data={upgrades} />
      </TabPanel>
      <TabPanel value={tabIndex} index={6}>
        <DiagramsTab data={diagrams} />
      </TabPanel>
      <TabPanel value={tabIndex} index={7}>
        <DocumentationTab analysisId={analysisId} />
      </TabPanel>
    </Box>
  );
}
