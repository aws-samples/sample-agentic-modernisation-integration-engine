import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import { Download as DownloadIcon, ExpandMore as ExpandMoreIcon } from '@mui/icons-material';
import {
  downloadDbConversionsSql,
  downloadDbSchemaSql,
  getDbConversions,
  reviewDbConversion,
  setDbConversionTests,
  startDbConversions,
} from '../services/api';
import type {
  BehaviourResult,
  BehaviourStatus,
  ConversionRecord,
  ConversionStatus,
  DbObject,
  DiscoveryReport,
  SandboxStatus,
} from '../types/dbDiscovery';

const CONVERTIBLE = ['procedure', 'function', 'view', 'trigger'];
const MAX_PER_REQUEST = 25;
const ACTIVE: ConversionStatus[] = ['queued', 'converting'];

const STATUS_COLOR: Record<ConversionStatus, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  queued: 'default',
  converting: 'info',
  converted: 'success',
  needs_review: 'warning',
  failed: 'error',
  approved: 'success',
  rejected: 'default',
};

const STATUS_LABEL: Record<ConversionStatus, string> = {
  queued: 'Queued',
  converting: 'Converting…',
  converted: 'Checks passed',
  needs_review: 'Needs review',
  failed: 'Failed',
  approved: 'Approved',
  rejected: 'Rejected',
};

export function ConversionStatusChip({ status }: { status: ConversionStatus }) {
  return (
    <Chip
      size="small"
      color={STATUS_COLOR[status]}
      variant={status === 'approved' ? 'filled' : 'outlined'}
      label={STATUS_LABEL[status]}
    />
  );
}

const BEHAVIOUR_LABEL: Record<BehaviourStatus, string> = {
  passed: 'Same results as SQL Server',
  failed: 'Differs from SQL Server',
  skipped: 'Not behaviour-tested',
  not_configured: 'Not behaviour-tested (no SQL Server)',
  error: 'Behaviour test error',
  not_run: 'Not behaviour-tested',
};

export function BehaviourChip({ status }: { status?: BehaviourStatus }) {
  if (!status) return null;
  const color = status === 'passed' ? 'success' : status === 'failed' || status === 'error' ? 'error' : 'default';
  return <Chip size="small" variant="outlined" color={color} label={BEHAVIOUR_LABEL[status]} data-testid="behaviour-chip" />;
}

export function BehaviourTable({ behaviour }: { behaviour: BehaviourResult }) {
  if (!behaviour.tests?.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        {behaviour.reason || BEHAVIOUR_LABEL[behaviour.status]}
      </Typography>
    );
  }
  return (
    <Table size="small" aria-label="behaviour tests">
      <TableHead>
        <TableRow>
          <TableCell>Test</TableCell>
          <TableCell>Inputs</TableCell>
          <TableCell>Result</TableCell>
          <TableCell>Detail</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {behaviour.tests.map((t, k) => (
          <TableRow key={`${t.name}-${k}`}>
            <TableCell>
              {t.name}
              {t.origin === 'custom' && <Chip size="small" label="yours" sx={{ ml: 1 }} />}
            </TableCell>
            <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>
              {t.params.map((p) => `${p.name}=${JSON.stringify(p.value)}`).join(', ') || '—'}
            </TableCell>
            <TableCell>
              <Chip
                size="small"
                variant="outlined"
                color={t.status === 'match' ? 'success' : t.status === 'mismatch' ? 'error' : 'default'}
                label={t.status === 'match' ? 'same' : t.status === 'mismatch' ? 'different' : 'skipped'}
              />
            </TableCell>
            <TableCell sx={{ fontSize: 12 }}>
              {[...t.details, t.source_error && `SQL Server: ${t.source_error}`, t.target_error && `MySQL: ${t.target_error}`]
                .filter(Boolean)
                .join(' · ') ||
                (t.result_rows ? `rows ${t.result_rows.source.join('/')} on both` : '')}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return err instanceof Error ? err.message : fallback;
}

function label(o: Pick<DbObject, 'schema' | 'name'>) {
  return o.schema ? `${o.schema}.${o.name}` : o.name;
}

// ─── Review dialog ────────────────────────────────────────────────────────────

export function ConversionReviewDialog({
  discoveryId,
  object,
  record,
  onClose,
  onSaved,
  onReconvert,
}: {
  discoveryId: string;
  object: DbObject | undefined;
  record: ConversionRecord;
  onClose: () => void;
  onSaved: (r: ConversionRecord, close?: boolean) => void;
  onReconvert?: (objectId: string) => void;
}) {
  const [sql, setSql] = useState(record.final_sql);
  const [note, setNote] = useState(record.reviewer_note);
  const [customTests, setCustomTests] = useState(
    record.custom_tests?.length ? JSON.stringify(record.custom_tests, null, 1) : ''
  );
  const [testsSaved, setTestsSaved] = useState(false);
  const latestBehaviour = [...record.attempts].reverse().find((a) => a.behaviour)?.behaviour;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const running = ACTIVE.includes(record.status);
  const edited = sql.trim() !== record.final_sql.trim();

  useEffect(() => {
    setSql(record.final_sql);
  }, [record.final_sql]);

  const act = async (action: 'approve' | 'reject' | 'edit') => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await reviewDbConversion(discoveryId, record.object_id, action, action === 'edit' ? sql : '', note));
    } catch (err) {
      setError(errorText(err, 'Review failed'));
    } finally {
      setBusy(false);
    }
  };

  const saveTests = async () => {
    setError(null);
    setTestsSaved(false);
    try {
      const parsed: unknown = customTests.trim() ? JSON.parse(customTests) : [];
      if (!Array.isArray(parsed)) throw new Error('Test cases must be a JSON array');
      onSaved(await setDbConversionTests(discoveryId, record.object_id, parsed as never), false);
      setTestsSaved(true);
    } catch (err) {
      setError(err instanceof SyntaxError ? `Test cases are not valid JSON: ${err.message}` : errorText(err, 'Saving tests failed'));
    }
  };

  return (
    <Dialog open fullWidth maxWidth="xl" onClose={onClose} aria-label="conversion review">
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box component="span" sx={{ fontFamily: 'monospace' }}>
          {record.object_type} {record.object_name}
        </Box>
        <ConversionStatusChip status={record.status} />
        <BehaviourChip status={record.behaviour_status} />
        {record.edited && <Chip size="small" label="edited by reviewer" />}
      </DialogTitle>
      <DialogContent dividers>
        {record.error && (
          <Alert severity={record.status === 'failed' ? 'error' : 'warning'} sx={{ mb: 2 }}>
            {record.error}
          </Alert>
        )}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <Box sx={{ display: 'flex', gap: 2, flexWrap: { xs: 'wrap', lg: 'nowrap' } }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle2" gutterBottom>
              SQL Server (source)
            </Typography>
            <Box
              component="pre"
              data-testid="source-sql"
              sx={{ m: 0, p: 1, bgcolor: 'grey.100', fontSize: 12, overflow: 'auto', maxHeight: 520, whiteSpace: 'pre' }}
            >
              {object?.source || '(source not available — re-run discovery)'}
            </Box>
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle2" gutterBottom>
              MySQL 8.0 (converted — editable)
            </Typography>
            <TextField
              multiline
              fullWidth
              minRows={20}
              maxRows={26}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              disabled={running}
              inputProps={{ 'aria-label': 'converted sql', style: { fontFamily: 'monospace', fontSize: 12 } }}
            />
          </Box>
        </Box>

        {record.notes && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2">Maker notes</Typography>
            <Typography variant="body2" component="pre" sx={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', m: 0 }}>
              {record.notes}
            </Typography>
          </Box>
        )}

        <Typography variant="subtitle2" sx={{ mt: 2 }}>
          Behaviour tests: SQL Server vs MySQL on the same data
        </Typography>
        {latestBehaviour ? (
          <BehaviourTable behaviour={latestBehaviour} />
        ) : (
          <Typography variant="body2" color="text.secondary">
            Not run: behaviour tests run after the conversion compiles and the checker passes it.
          </Typography>
        )}
        {record.copy_problems && record.copy_problems.length > 0 && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            Not copied to the sandbox: {record.copy_problems.map((p) => `${p.table} (${p.reason})`).join('; ')}
          </Alert>
        )}
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', mt: 1 }}>
          <TextField
            label="Your test cases (JSON, run with the generated ones on the next conversion)"
            multiline
            minRows={2}
            fullWidth
            size="small"
            value={customTests}
            placeholder='[{"name": "no matches", "params": [{"name": "SearchText", "value": "zzzz"}]}]'
            onChange={(e) => {
              setCustomTests(e.target.value);
              setTestsSaved(false);
            }}
            inputProps={{ 'aria-label': 'custom tests', style: { fontFamily: 'monospace', fontSize: 12 } }}
          />
          <Button variant="outlined" onClick={() => void saveTests()} disabled={running}>
            {testsSaved ? 'Saved' : 'Save tests'}
          </Button>
        </Box>

        <Typography variant="subtitle2" sx={{ mt: 2 }}>
          Attempts ({record.attempts.length})
        </Typography>
        {record.attempts.map((a) => (
          <Accordion key={a.n} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Typography variant="body2">Attempt {a.n}</Typography>
                <Chip
                  size="small"
                  color={a.compile?.ok ? 'success' : 'error'}
                  variant="outlined"
                  label={a.compile?.ok ? 'compiles in MySQL 8.0' : `MySQL error ${a.compile?.errno ?? ''}`}
                />
                {a.judge && (
                  <Chip
                    size="small"
                    color={a.judge.verdict === 'pass' ? 'success' : 'warning'}
                    variant="outlined"
                    label={`checker: ${a.judge.verdict} (min ${Math.min(...Object.values(a.judge.scores))}/5)`}
                  />
                )}
                {a.behaviour && <BehaviourChip status={a.behaviour.status} />}
              </Box>
            </AccordionSummary>
            <AccordionDetails>
              {a.compile && !a.compile.ok && (
                <Alert severity="error" sx={{ mb: 1 }}>
                  {a.compile.message}
                </Alert>
              )}
              {a.judge && (
                <Box sx={{ mb: 1 }}>
                  <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 1 }}>
                    {Object.entries(a.judge.scores).map(([k, v]) => (
                      <Chip key={k} size="small" label={`${k.replace(/_/g, ' ')}: ${v}`} color={v >= 4 ? 'default' : 'warning'} />
                    ))}
                  </Box>
                  {a.judge.issues.map((i, k) => (
                    <Typography key={k} variant="body2" color="text.secondary">
                      • {i}
                    </Typography>
                  ))}
                </Box>
              )}
              {a.behaviour?.tests && a.behaviour.tests.length > 0 && (
                <Box sx={{ mb: 1 }}>
                  <BehaviourTable behaviour={a.behaviour} />
                </Box>
              )}
              <Box component="pre" sx={{ m: 0, p: 1, bgcolor: 'grey.50', fontSize: 11, overflow: 'auto', maxHeight: 240 }}>
                {a.sql}
              </Box>
            </AccordionDetails>
          </Accordion>
        ))}

        <TextField
          sx={{ mt: 2 }}
          fullWidth
          size="small"
          label="Reviewer note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          disabled={running}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {onReconvert && (
          <Button
            disabled={busy || running}
            onClick={() => {
              if (record.status === 'approved' && !window.confirm('Re-converting replaces the approved version. Continue?')) return;
              onReconvert(record.object_id);
            }}
          >
            Re-convert
          </Button>
        )}
        <Button color="error" disabled={busy || running} onClick={() => void act('reject')}>
          Reject
        </Button>
        {edited ? (
          <Button variant="contained" color="secondary" disabled={busy || running || !sql.trim()} onClick={() => void act('edit')}>
            Compile edit &amp; approve
          </Button>
        ) : (
          <Button variant="contained" color="secondary" disabled={busy || running || !record.final_sql} onClick={() => void act('approve')}>
            Approve
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

// ─── Panel ────────────────────────────────────────────────────────────────────

export function DbConversionPanel({ report }: { report: DiscoveryReport }) {
  const id = report.discovery_id;
  const [records, setRecords] = useState<Record<string, ConversionRecord>>({});
  const [sandbox, setSandbox] = useState<SandboxStatus | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [type, setType] = useState('all');
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const objects = useMemo(
    () =>
      report.objects
        .filter((o) => CONVERTIBLE.includes(o.type))
        .sort((a, b) => b.effort_points - a.effort_points),
    [report.objects]
  );
  const byId = useMemo(() => new Map(report.objects.map((o) => [o.id, o])), [report.objects]);

  const load = useCallback(async () => {
    try {
      const res = await getDbConversions(id);
      setRecords(Object.fromEntries(res.conversions.map((r) => [r.object_id, r])));
      setSandbox(res.sandbox);
    } catch (err) {
      setError(errorText(err, 'Could not load conversions'));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const active = Object.values(records).some((r) => ACTIVE.includes(r.status));
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [active, load]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return objects
      .filter((o) => type === 'all' || o.type === type)
      .filter((o) => !q || label(o).toLowerCase().includes(q));
  }, [objects, type, search]);

  const counts = useMemo(() => {
    const c: Partial<Record<ConversionStatus, number>> = {};
    Object.values(records).forEach((r) => (c[r.status] = (c[r.status] ?? 0) + 1));
    return c;
  }, [records]);

  const toggle = (oid: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(oid)) next.delete(oid);
      else if (next.size < MAX_PER_REQUEST) next.add(oid);
      return next;
    });

  const start = async () => {
    setError(null);
    setInfo(null);
    try {
      const res = await startDbConversions(id, Array.from(selected));
      setInfo(
        `Queued ${res.queued.length} object(s)` + (res.skipped.length ? `; skipped ${res.skipped.length} already running.` : '.')
      );
      setSelected(new Set());
      await load();
    } catch (err) {
      setError(errorText(err, 'Could not start conversion'));
    }
  };

  const reconvert = async (objectId: string) => {
    setError(null);
    setInfo(null);
    try {
      const res = await startDbConversions(id, [objectId]);
      const name = records[objectId]?.object_name ?? objectId;
      setInfo(res.queued.length ? `Re-converting ${name}. Previous test cases are kept.` : `${name} is already running.`);
      setOpen(null);
      await load();
    } catch (err) {
      setError(errorText(err, 'Could not start conversion'));
    }
  };

  const download = (fn: (i: string) => Promise<void>) => fn(id).catch((err: unknown) => setError(errorText(err, 'Download failed')));
  const openRecord = open ? records[open] : undefined;
  const sandboxDown = sandbox !== null && !sandbox.reachable;

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 2, flexWrap: 'wrap', mb: 2 }}>
        <Box sx={{ maxWidth: 820 }}>
          <Typography variant="body2" color="text.secondary">
            The Maker (Bedrock) converts each object to MySQL 8.0 using its findings and the generated table DDL. Checkers then
            compile it in the MySQL sandbox, have a second model score equivalence, and, when a SQL Server source is configured,
            run the original and the conversion with the same inputs on the same data and compare results. Any failure goes back
            to the Maker. You approve, edit or reject the result; only approved conversions are exported.
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap' }}>
            {sandbox && (
              <Chip
                size="small"
                data-testid="sandbox-status"
                color={sandbox.reachable ? 'success' : 'error'}
                variant="outlined"
                label={sandbox.reachable ? `MySQL sandbox ${sandbox.version}` : 'MySQL sandbox unreachable'}
              />
            )}
            {(Object.keys(counts) as ConversionStatus[]).map((s) => (
              <Chip key={s} size="small" label={`${STATUS_LABEL[s]}: ${counts[s]}`} />
            ))}
          </Box>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button variant="outlined" startIcon={<DownloadIcon />} onClick={() => void download(downloadDbSchemaSql)}>
            schema.sql
          </Button>
          <Button variant="outlined" startIcon={<DownloadIcon />} onClick={() => void download(downloadDbConversionsSql)}>
            Approved conversions
          </Button>
        </Box>
      </Box>

      {sandboxDown && <Alert severity="warning" sx={{ mb: 2 }}>{sandbox?.error ?? 'The MySQL sandbox is not reachable; conversions will fail.'}</Alert>}
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {info && <Alert severity="info" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

      <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel id="conv-type">Type</InputLabel>
          <Select labelId="conv-type" label="Type" value={type} onChange={(e) => setType(e.target.value)}>
            <MenuItem value="all">All</MenuItem>
            {CONVERTIBLE.map((t) => (
              <MenuItem key={t} value={t}>
                {t}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField size="small" label="Search" value={search} onChange={(e) => setSearch(e.target.value)} />
        <Button variant="contained" color="secondary" disabled={selected.size === 0} onClick={() => void start()}>
          Convert selected ({selected.size})
        </Button>
        <Typography variant="caption" color="text.secondary">
          Up to {MAX_PER_REQUEST} per run. Each object takes a few minutes with Opus.
        </Typography>
      </Box>

      <Table size="small" aria-label="convertible objects">
        <TableHead>
          <TableRow>
            <TableCell padding="checkbox" />
            <TableCell>Object</TableCell>
            <TableCell>Type</TableCell>
            <TableCell align="right">LOC</TableCell>
            <TableCell>Effort</TableCell>
            <TableCell>Conversion</TableCell>
            <TableCell>Behaviour</TableCell>
            <TableCell align="right">Attempts</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((o) => {
            const r = records[o.id];
            const busy = r && ACTIVE.includes(r.status);
            return (
              <TableRow key={o.id} hover>
                <TableCell padding="checkbox">
                  <Checkbox
                    size="small"
                    checked={selected.has(o.id)}
                    disabled={busy || (!selected.has(o.id) && selected.size >= MAX_PER_REQUEST)}
                    onChange={() => toggle(o.id)}
                    inputProps={{ 'aria-label': `select ${label(o)}` }}
                  />
                </TableCell>
                <TableCell sx={{ fontFamily: 'monospace' }}>{label(o)}</TableCell>
                <TableCell>{o.type}</TableCell>
                <TableCell align="right">{o.lines_of_code}</TableCell>
                <TableCell>{o.effort.replace('_', ' ')}</TableCell>
                <TableCell>{r ? <ConversionStatusChip status={r.status} /> : '—'}</TableCell>
                <TableCell>{r && !busy ? <BehaviourChip status={r.behaviour_status ?? 'not_run'} /> : ''}</TableCell>
                <TableCell align="right">{r?.attempts.length || ''}</TableCell>
                <TableCell align="right">
                  {r && !busy && (
                    <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 0.5 }}>
                      <Button size="small" onClick={() => setOpen(o.id)} aria-label={`review ${label(o)}`}>
                        Review
                      </Button>
                      <Button
                        size="small"
                        color="secondary"
                        aria-label={`re-convert ${label(o)}`}
                        onClick={() => {
                          if (r.status === 'approved' && !window.confirm('Re-converting replaces the approved version. Continue?')) return;
                          void reconvert(o.id);
                        }}
                      >
                        Re-convert
                      </Button>
                    </Box>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      {openRecord && (
        <ConversionReviewDialog
          discoveryId={id}
          object={byId.get(openRecord.object_id)}
          record={openRecord}
          onClose={() => setOpen(null)}
          onReconvert={(oid) => void reconvert(oid)}
          onSaved={(r, close = true) => {
            setRecords((prev) => ({ ...prev, [r.object_id]: r }));
            if (close) setOpen(null);
          }}
        />
      )}
    </Box>
  );
}
