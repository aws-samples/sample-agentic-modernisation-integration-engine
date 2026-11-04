import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  LinearProgress,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import {
  getDbValidationRun,
  getDbValidationRuns,
  getDbValidationStatus,
  startDbCopy,
  startDbValidation,
} from '../services/api';
import type { ConnectionStatus, DiscoveryReport, ProcedureTestInput, ValidationRun, ValidationStatus } from '../types/dbDiscovery';

const RESULT_COLOR: Record<string, 'success' | 'error' | 'warning' | 'default'> = {
  match: 'success',
  copied: 'success',
  mismatch: 'error',
  not_loaded: 'warning',
  missing_in_source: 'default',
  missing_in_target: 'warning',
  error: 'error',
};

const RESULT_LABEL: Record<string, string> = {
  match: 'match',
  copied: 'copied',
  mismatch: 'mismatch',
  not_loaded: 'not loaded',
  missing_in_source: 'missing in source',
  missing_in_target: 'missing in target',
  error: 'error',
};

const label = (status: string) => RESULT_LABEL[status] ?? status;

/** Tables an object reads or writes: direct references plus those of views/functions it uses. */
export function tablesUsedBy(report: DiscoveryReport, objectId: string): string[] {
  const byId = new Map(report.objects.map((o) => [o.id, o]));
  const found = new Set<string>();
  const seen = new Set<string>([objectId]);
  let frontier = [objectId];
  for (let depth = 0; depth < 3 && frontier.length; depth++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const ref of byId.get(id)?.references ?? []) {
        if (seen.has(ref)) continue;
        seen.add(ref);
        const o = byId.get(ref);
        if (o?.type === 'table') found.add(ref);
        else if (o && ['view', 'function', 'procedure'].includes(o.type)) next.push(ref);
      }
    }
    frontier = next;
  }
  return Array.from(found).sort();
}

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return err instanceof Error ? err.message : fallback;
}

function ConnectionCard({ title, c }: { title: string; c: ConnectionStatus }) {
  return (
    <Card variant="outlined" sx={{ flex: '1 1 300px' }}>
      <CardContent>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="subtitle1">{title}</Typography>
          <Chip
            size="small"
            data-testid={`${c.engine}-status`}
            color={c.reachable ? 'success' : c.configured ? 'error' : 'default'}
            label={c.reachable ? 'reachable' : c.configured ? 'unreachable' : 'not configured'}
          />
        </Box>
        {c.configured && (
          <Typography variant="body2" color="text.secondary" sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
            {c.user}@{c.host}:{c.port}
            {c.database ? `/${c.database}` : ''}
            {c.sandbox ? ' (sandbox)' : ''}
          </Typography>
        )}
        {c.version && <Typography variant="caption" color="text.secondary">{c.version}</Typography>}
        {c.error && (
          <Typography variant="caption" color={c.configured ? 'error' : 'text.secondary'} display="block">
            {c.error}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

const EXAMPLE_TESTS = `[
  {"procedure": "Website.SearchForCustomers",
   "params": [{"name": "SearchText", "value": "Toys"}, {"name": "MaximumRowsToReturn", "value": 10}]}
]`;

export function parseProcedureTests(text: string): ProcedureTestInput[] {
  if (!text.trim()) return [];
  const parsed: unknown = JSON.parse(text);
  if (!Array.isArray(parsed)) throw new Error('Procedure tests must be a JSON array');
  return parsed as ProcedureTestInput[];
}

export function DbValidationPanel({ report }: { report: DiscoveryReport }) {
  const [status, setStatus] = useState<ValidationStatus | null>(null);
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<ValidationRun | null>(null);
  const [tables, setTables] = useState<string[]>([]);
  const [tests, setTests] = useState('');
  const [maxRows, setMaxRows] = useState(1000);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const objectOptions = useMemo(
    () =>
      report.objects
        .filter((o) => ['procedure', 'function', 'view'].includes(o.type))
        .map((o) => ({ id: o.id, label: `${o.type} ${o.schema}.${o.name}` })),
    [report.objects]
  );
  const [usedBy, setUsedBy] = useState<{ id: string; label: string } | null>(null);

  const tableOptions = useMemo(
    () => report.objects.filter((o) => o.type === 'table').map((o) => ({ id: o.id, label: `${o.schema}.${o.name}` })),
    [report.objects]
  );

  const loadRuns = useCallback(async () => {
    try {
      const all = await getDbValidationRuns();
      setRuns(all.filter((r) => r.discovery_id === report.discovery_id));
    } catch (err) {
      setError(errorText(err, 'Could not load runs'));
    }
  }, [report.discovery_id]);

  useEffect(() => {
    getDbValidationStatus()
      .then(setStatus)
      .catch((err: unknown) => setError(errorText(err, 'Could not load connection status')));
    void loadRuns();
  }, [loadRuns]);

  const processing = runs.some((r) => r.status === 'processing');
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(() => void loadRuns(), 3000);
    return () => clearInterval(t);
  }, [processing, loadRuns]);

  const openRun = async (runId: string) => {
    try {
      setSelectedRun(await getDbValidationRun(runId));
    } catch (err) {
      setError(errorText(err, 'Could not load run'));
    }
  };

  useEffect(() => {
    if (!selectedRun || selectedRun.status !== 'processing') return;
    const r = runs.find((x) => x.run_id === selectedRun.run_id);
    if (r && r.status !== 'processing') void openRun(r.run_id);
  }, [runs, selectedRun]);

  const run = async (kind: 'validation' | 'copy') => {
    setError(null);
    setBusy(true);
    try {
      const res =
        kind === 'validation'
          ? await startDbValidation(report.discovery_id, tables, parseProcedureTests(tests))
          : await startDbCopy(report.discovery_id, tables, maxRows);
      await loadRuns();
      await openRun(res.run_id);
    } catch (err) {
      setError(err instanceof SyntaxError ? `Procedure tests are not valid JSON: ${err.message}` : errorText(err, 'Run failed to start'));
    } finally {
      setBusy(false);
    }
  };

  const sourceReady = status?.source.configured && status.target.configured;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Typography variant="body2" color="text.secondary">
        Compares the SQL Server source with the MySQL target: row counts, an order-independent checksum of normalised values
        per table, and stored-procedure result sets. Connections come from the backend environment; see{' '}
        <code>infra/terraform/rds-sqlserver-test</code> for a test SQL Server with WideWorldImporters.
      </Typography>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      {status && (
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          <ConnectionCard title="Source: SQL Server" c={status.source} />
          <ConnectionCard title="Target: MySQL" c={status.target} />
        </Box>
      )}

      <Card variant="outlined">
        <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Autocomplete
            size="small"
            options={objectOptions}
            value={usedBy}
            onChange={(_, v) => {
              setUsedBy(v);
              if (v) setTables(tablesUsedBy(report, v.id));
            }}
            renderInput={(params) => (
              <TextField {...params} label="Select the tables used by a procedure, function or view (optional)" />
            )}
          />
          <Autocomplete
            multiple
            size="small"
            options={tableOptions}
            value={tableOptions.filter((o) => tables.includes(o.id))}
            onChange={(_, v) => setTables(v.map((x) => x.id))}
            renderInput={(params) => <TextField {...params} label="Tables (empty = all tables)" />}
          />
          <TextField
            label="Procedure tests (JSON, optional)"
            multiline
            minRows={3}
            value={tests}
            placeholder={EXAMPLE_TESTS}
            onChange={(e) => setTests(e.target.value)}
            inputProps={{ style: { fontFamily: 'monospace', fontSize: 12 }, 'aria-label': 'procedure tests' }}
          />
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button variant="contained" color="secondary" disabled={!sourceReady || busy} onClick={() => void run('validation')}>
              Run validation
            </Button>
            {status?.copy_enabled && (
              <>
                <TextField
                  size="small"
                  type="number"
                  label="Rows per table"
                  value={maxRows}
                  onChange={(e) => setMaxRows(Math.max(1, Math.min(100000, Number(e.target.value) || 1)))}
                  sx={{ width: 150 }}
                />
                <Button variant="outlined" color="warning" disabled={!sourceReady || busy} onClick={() => void run('copy')}>
                  Copy test data to target
                </Button>
              </>
            )}
            {!sourceReady && status && (
              <Typography variant="caption" color="text.secondary">
                Configure VALIDATION_SQLSERVER_* in .env to enable runs.
              </Typography>
            )}
          </Box>
        </CardContent>
      </Card>

      <Typography variant="h6">Runs</Typography>
      {runs.length === 0 ? (
        <Typography variant="body2" color="text.secondary">No runs for this discovery yet.</Typography>
      ) : (
        <Table size="small" aria-label="validation runs">
          <TableHead>
            <TableRow>
              <TableCell>Run</TableCell>
              <TableCell>Kind</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Summary</TableCell>
              <TableCell>Started</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {runs.map((r) => (
              <TableRow key={r.run_id} hover selected={selectedRun?.run_id === r.run_id} onClick={() => void openRun(r.run_id)} sx={{ cursor: 'pointer' }}>
                <TableCell sx={{ fontFamily: 'monospace' }}>{r.run_id}</TableCell>
                <TableCell>{r.kind}</TableCell>
                <TableCell>
                  {r.status === 'processing' ? (
                    <Box sx={{ minWidth: 160 }}>
                      <LinearProgress variant="determinate" value={r.progress} />
                      <Typography variant="caption">{r.message}</Typography>
                    </Box>
                  ) : (
                    <Chip size="small" color={r.status === 'completed' ? 'success' : 'error'} variant="outlined" label={r.status} />
                  )}
                </TableCell>
                <TableCell>
                  {Object.entries(r.summary ?? {}).map(([k, v]) => (
                    <Chip key={k} size="small" sx={{ mr: 0.5 }} color={RESULT_COLOR[k] ?? 'default'} variant="outlined" label={`${label(k)} ${v}`} />
                  ))}
                  {r.status === 'failed' && <Typography variant="caption" color="error">{r.message}</Typography>}
                </TableCell>
                <TableCell>{r.started_at}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {selectedRun?.result && (
        <Box>
          <Typography variant="h6" gutterBottom>
            {selectedRun.run_id}
          </Typography>
          <Table size="small" aria-label="table results">
            <TableHead>
              <TableRow>
                <TableCell>Table</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Source rows</TableCell>
                <TableCell align="right">Target rows</TableCell>
                <TableCell>Checksums / detail</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {selectedRun.result.tables.map((t) => (
                <TableRow key={t.object_id}>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{t.table}</TableCell>
                  <TableCell>
                    <Chip size="small" color={RESULT_COLOR[t.status] ?? 'default'} variant="outlined" label={label(t.status)} />
                  </TableCell>
                  <TableCell align="right">{t.source_rows ?? t.rows ?? ''}</TableCell>
                  <TableCell align="right">{t.target_rows ?? ''}</TableCell>
                  <TableCell sx={{ fontSize: 12 }}>
                    {t.error ||
                      (t.source_checksum && (
                        <span style={{ fontFamily: 'monospace' }}>
                          {t.source_checksum.slice(0, 12)} / {t.target_checksum.slice(0, 12)}
                        </span>
                      ))}
                    {t.excluded_columns?.length > 0 && (
                      <Typography variant="caption" display="block" color="text.secondary">
                        not compared: {t.excluded_columns.map((e) => e.column).join(', ')}
                      </Typography>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {(selectedRun.result.procedures ?? []).length > 0 && (
            <Table size="small" aria-label="procedure results" sx={{ mt: 2 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Procedure</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Detail</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {selectedRun.result.procedures?.map((p, k) => (
                  <TableRow key={`${p.procedure}-${k}`}>
                    <TableCell sx={{ fontFamily: 'monospace' }}>{p.procedure}</TableCell>
                    <TableCell>
                      <Chip size="small" color={RESULT_COLOR[p.status] ?? 'default'} variant="outlined" label={label(p.status)} />
                    </TableCell>
                    <TableCell sx={{ fontSize: 12 }}>{p.error || p.details.join('; ') || 'identical result sets'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Box>
      )}
    </Box>
  );
}
