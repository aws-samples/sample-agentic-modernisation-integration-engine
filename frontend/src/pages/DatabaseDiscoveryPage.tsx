import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  IconButton,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { Delete as DeleteIcon, GitHub as GitHubIcon, OpenInNew as OpenIcon } from '@mui/icons-material';
import { FileUpload } from '../components/FileUpload';
import { ProgressTracker } from '../components/ProgressTracker';
import { DbPathwayDiagram } from '../components/DbPathwayDiagram';
import {
  deleteDbDiscovery,
  getDbDiscoveries,
  getDbDiscoveryStatus,
  startDbDiscoveryGithub,
  startDbDiscoveryUpload,
} from '../services/api';
import type { DiscoveryListItem } from '../types/dbDiscovery';

const DISCOVERY_STEPS = ['Scanning', 'Parsing T-SQL', 'MySQL rules', 'Application code', 'Summary'];

function discoveryStepIndex(step: string): number {
  switch (step) {
    case 'parsing':
      return 1;
    case 'rules':
      return 2;
    case 'application':
      return 3;
    case 'summary':
    case 'done':
      return 4;
    default:
      return 0;
  }
}

/** Prefills the GitHub form with Microsoft's WideWorldImporters SSDT project. */
export const WWI_SAMPLE = {
  repoUrl: 'https://github.com/microsoft/sql-server-samples',
  branch: 'master',
  subpath: 'samples/databases/wide-world-importers/wwi-ssdt',
};

function errorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return err instanceof Error ? err.message : fallback;
}

export function DatabaseDiscoveryPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState(0);
  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('main');
  const [subpath, setSubpath] = useState('');
  const [patToken, setPatToken] = useState('');
  const [discoveryId, setDiscoveryId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [history, setHistory] = useState<DiscoveryListItem[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await getDbDiscoveries());
      setHistoryError(null);
    } catch (err) {
      setHistoryError(errorMessage(err, 'Could not load previous discoveries'));
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const handleGithub = async (e: FormEvent) => {
    e.preventDefault();
    if (!repoUrl.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await startDbDiscoveryGithub(repoUrl.trim(), branch.trim() || 'main', subpath.trim(), patToken.trim() || undefined);
      setDiscoveryId(res.discovery_id);
    } catch (err) {
      setError(errorMessage(err, 'Discovery failed to start'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleUpload = useCallback(async (file: File) => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await startDbDiscoveryUpload(file);
      setDiscoveryId(res.discovery_id);
    } catch (err) {
      setError(errorMessage(err, 'Upload failed'));
    } finally {
      setSubmitting(false);
    }
  }, []);

  const handleComplete = useCallback((id: string) => navigate(`/database-discovery/${id}`), [navigate]);
  const handleFailed = useCallback((_id: string, message: string) => {
    setError(message);
    setDiscoveryId(null);
    void loadHistory();
  }, [loadHistory]);

  const handleDelete = async (id: string) => {
    try {
      await deleteDbDiscovery(id);
      await loadHistory();
    } catch (err) {
      setHistoryError(errorMessage(err, 'Delete failed'));
    }
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Database Discovery: SQL Server → Aurora MySQL
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Inventory T-SQL objects (.sql scripts or an SSDT project) and the .NET code that calls them, and flag every
        construct that needs work on Aurora MySQL 3 (MySQL 8.0). Analysis is deterministic: no model is called.
      </Typography>

      <DbPathwayDiagram />

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {discoveryId ? (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="subtitle2" color="text.secondary">
              Discovery {discoveryId}
            </Typography>
            <ProgressTracker
              analysisId={discoveryId}
              onComplete={handleComplete}
              onFailed={handleFailed}
              fetchStatus={getDbDiscoveryStatus}
              steps={DISCOVERY_STEPS}
              stepIndexFor={discoveryStepIndex}
            />
          </CardContent>
        </Card>
      ) : (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Tabs value={tab} onChange={(_, v: number) => setTab(v)} sx={{ mb: 1 }}>
              <Tab label="GitHub" />
              <Tab label="ZIP Upload" />
            </Tabs>
            {tab === 0 && (
              <Box component="form" onSubmit={handleGithub} sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
                <TextField
                  label="Repository URL"
                  placeholder="https://github.com/owner/repo"
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  required
                  fullWidth
                  InputProps={{ startAdornment: <GitHubIcon sx={{ mr: 1, color: 'text.secondary' }} /> }}
                />
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <TextField label="Branch" value={branch} onChange={(e) => setBranch(e.target.value)} sx={{ flex: 1 }} />
                  <TextField
                    label="Sub-path (optional)"
                    placeholder="path/inside/repo"
                    value={subpath}
                    onChange={(e) => setSubpath(e.target.value)}
                    helperText="Only this folder is checked out (sparse clone)"
                    sx={{ flex: 2 }}
                  />
                </Box>
                <TextField
                  label="PAT Token (optional)"
                  type="password"
                  value={patToken}
                  onChange={(e) => setPatToken(e.target.value)}
                  helperText="Required for private repositories"
                  fullWidth
                />
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <Button type="submit" variant="contained" color="secondary" disabled={!repoUrl.trim() || submitting}>
                    {submitting ? 'Starting...' : 'Run Discovery'}
                  </Button>
                  <Button
                    variant="outlined"
                    onClick={() => {
                      setRepoUrl(WWI_SAMPLE.repoUrl);
                      setBranch(WWI_SAMPLE.branch);
                      setSubpath(WWI_SAMPLE.subpath);
                    }}
                  >
                    Use WideWorldImporters sample
                  </Button>
                </Box>
              </Box>
            )}
            {tab === 1 && (
              <Box sx={{ pt: 2 }}>
                <FileUpload onFileSelect={handleUpload} />
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  {submitting
                    ? 'Uploading...'
                    : 'A ZIP of .sql scripts and/or .NET source (.cs, .config, .csproj). samples/wwi-legacy-dotnet/build-demo-zip.sh builds a demo archive.'}
                </Typography>
              </Box>
            )}
          </CardContent>
        </Card>
      )}

      <Typography variant="h6" gutterBottom>
        Previous discoveries
      </Typography>
      {historyError && <Alert severity="warning" sx={{ mb: 2 }}>{historyError}</Alert>}
      {history.length === 0 && !historyError ? (
        <Typography variant="body2" color="text.secondary">
          No discoveries yet.
        </Typography>
      ) : (
        <Table size="small" aria-label="previous discoveries">
          <TableHead>
            <TableRow>
              <TableCell>Discovery</TableCell>
              <TableCell>Source</TableCell>
              <TableCell>Completed</TableCell>
              <TableCell align="right">Objects</TableCell>
              <TableCell>Findings</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {history.map((d) => {
              const objects = Object.values(d.object_counts ?? {}).reduce((a, b) => a + b, 0);
              return (
                <TableRow key={d.discovery_id} hover>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{d.discovery_id}</TableCell>
                  <TableCell>
                    {d.source_url || 'ZIP upload'}
                    {d.subpath && (
                      <Typography variant="caption" display="block" color="text.secondary">
                        {d.subpath}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>{d.completed_at}</TableCell>
                  <TableCell align="right">{objects}</TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', gap: 0.5 }}>
                      {(['blocker', 'high', 'medium'] as const).map((s) => (
                        <Chip key={s} size="small" label={`${s} ${d.finding_counts?.[s] ?? 0}`} />
                      ))}
                    </Box>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="Open">
                      <IconButton size="small" aria-label={`open ${d.discovery_id}`} onClick={() => navigate(`/database-discovery/${d.discovery_id}`)}>
                        <OpenIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Delete">
                      <IconButton size="small" aria-label={`delete ${d.discovery_id}`} onClick={() => void handleDelete(d.discovery_id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </Box>
  );
}
