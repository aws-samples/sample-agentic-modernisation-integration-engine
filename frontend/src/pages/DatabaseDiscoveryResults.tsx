import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  FormControl,
  IconButton,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TablePagination,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import {
  ArrowBack as BackIcon,
  ArrowForward as ForwardIcon,
  Close as CloseIcon,
  Download as DownloadIcon,
} from '@mui/icons-material';
import { downloadDbDiscoveryMarkdown, getDbDiscovery, saveBlob } from '../services/api';
import {
  EFFORTS,
  SEVERITIES,
  type DbFinding,
  type DbObject,
  type DiscoveryReport,
  type Effort,
  type Severity,
} from '../types/dbDiscovery';

// ─── Shared bits ──────────────────────────────────────────────────────────────

const SEVERITY_COLOR: Record<Severity, 'error' | 'warning' | 'info' | 'default'> = {
  blocker: 'error',
  high: 'error',
  medium: 'warning',
  low: 'info',
  info: 'default',
};

export function SeverityChip({ severity }: { severity: Severity }) {
  return (
    <Chip
      size="small"
      label={severity}
      color={SEVERITY_COLOR[severity]}
      variant={severity === 'blocker' ? 'filled' : 'outlined'}
      sx={severity === 'blocker' ? { fontWeight: 700 } : undefined}
    />
  );
}

const EFFORT_LABEL: Record<Effort, string> = { low: 'Low', medium: 'Medium', high: 'High', very_high: 'Very high' };
const EFFORT_COLOR: Record<Effort, 'success' | 'info' | 'warning' | 'error'> = {
  low: 'success',
  medium: 'info',
  high: 'warning',
  very_high: 'error',
};

function EffortChip({ effort }: { effort: Effort }) {
  return <Chip size="small" label={EFFORT_LABEL[effort]} color={EFFORT_COLOR[effort]} variant="outlined" />;
}

function objectLabel(o: Pick<DbObject, 'schema' | 'name'>): string {
  return o.schema ? `${o.schema}.${o.name}` : o.name;
}

function usePaged<T>(rows: T[], perPage = 25) {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(perPage);
  useEffect(() => setPage(0), [rows.length]);
  const slice = rows.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage);
  const pagination = (
    <TablePagination
      component="div"
      count={rows.length}
      page={page}
      rowsPerPage={rowsPerPage}
      rowsPerPageOptions={[25, 50, 100]}
      onPageChange={(_, p: number) => setPage(p)}
      onRowsPerPageChange={(e) => {
        setRowsPerPage(parseInt(e.target.value, 10));
        setPage(0);
      }}
    />
  );
  return { slice, pagination };
}

function StatCard({ label, value, caption }: { label: string; value: string | number; caption?: string }) {
  return (
    <Card variant="outlined" sx={{ flex: '1 1 180px' }}>
      <CardContent>
        <Typography variant="body2" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="h4">{value}</Typography>
        {caption && (
          <Typography variant="caption" color="text.secondary">
            {caption}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

function Bars({ data, total }: { data: [string, number][]; total: number }) {
  return (
    <Box>
      {data.map(([label, value]) => (
        <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
          <Typography variant="body2" sx={{ width: 160, flexShrink: 0 }}>
            {label}
          </Typography>
          <LinearProgress
            variant="determinate"
            value={total ? (value / total) * 100 : 0}
            sx={{ flex: 1, height: 8, borderRadius: 4 }}
            color="secondary"
          />
          <Typography variant="body2" sx={{ width: 48, textAlign: 'right' }}>
            {value}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

// ─── Overview ─────────────────────────────────────────────────────────────────

export function OverviewTab({ report }: { report: DiscoveryReport }) {
  const s = report.summary;
  const totalObjects = report.objects.length;
  const totalFindings = SEVERITIES.reduce((a, k) => a + (s.finding_counts[k] ?? 0), 0);
  const ld = s.logic_distribution;
  const categories = Object.entries(s.category_counts);
  const types = Object.entries(s.object_counts);
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
        <StatCard label="Database objects" value={totalObjects} caption={`${s.schemas.length} schemas`} />
        <StatCard label="Lines of T-SQL" value={s.total_sql_lines.toLocaleString()} caption={`${s.files_scanned.sql} SQL files`} />
        <StatCard label="Findings" value={totalFindings} caption={`${s.finding_counts.blocker} blocker, ${s.finding_counts.high} high`} />
        <StatCard label="Objects needing manual work" value={s.objects_needing_manual_work} caption="high or very high effort" />
        <StatCard
          label="Application SQL statements"
          value={s.application.embedded_sql_statements}
          caption={`${ld.dynamic_embedded_sql_statements} built dynamically`}
        />
      </Box>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
        <Card variant="outlined" sx={{ flex: '1 1 320px' }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Findings by severity
            </Typography>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 2 }}>
              {SEVERITIES.map((sev) => (
                <Box key={sev} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <SeverityChip severity={sev} />
                  <Typography variant="body2" data-testid={`severity-count-${sev}`}>
                    {s.finding_counts[sev] ?? 0}
                  </Typography>
                </Box>
              ))}
            </Box>
            <Typography variant="h6" gutterBottom>
              Effort by object
            </Typography>
            <Bars data={EFFORTS.map((e) => [EFFORT_LABEL[e], s.effort_counts[e] ?? 0])} total={totalObjects} />
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
              {s.effort_formula}
            </Typography>
          </CardContent>
        </Card>
        <Card variant="outlined" sx={{ flex: '1 1 320px' }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Where the logic lives
            </Typography>
            <Typography variant="body2" gutterBottom>
              {ld.database_modules} procedures, functions and triggers with {ld.database_module_lines.toLocaleString()} lines of T-SQL.
            </Typography>
            <Typography variant="body2" gutterBottom>
              {ld.embedded_sql_statements} SQL statements embedded in application code ({ld.dynamic_embedded_sql_statements} built
              dynamically).
            </Typography>
            <Typography variant="body2" gutterBottom>
              {s.application.procedures_called_from_code} procedures are called from the scanned code;{' '}
              {s.application.procedures_not_called_from_code} are not.
              {s.application.unresolved_procedure_calls > 0 &&
                ` ${s.application.unresolved_procedure_calls} procedure calls in code name no procedure in the scripts.`}
            </Typography>
            <Divider sx={{ my: 1.5 }} />
            <Typography variant="body2" color="text.secondary">
              sqlglot parsed {s.ast_parse.parsed} of {s.ast_parse.batches} T-SQL batches into an AST; the inventory and rules do not
              depend on that parse.
            </Typography>
            {s.application.technologies.length > 0 && (
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 1.5 }}>
                {s.application.technologies.map((t) => (
                  <Chip key={t} size="small" label={t} />
                ))}
              </Box>
            )}
          </CardContent>
        </Card>
      </Box>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
        <Card variant="outlined" sx={{ flex: '2 1 480px' }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Top rules
            </Typography>
            <Table size="small" aria-label="top rules">
              <TableHead>
                <TableRow>
                  <TableCell>Rule</TableCell>
                  <TableCell>Severity</TableCell>
                  <TableCell align="right">Objects</TableCell>
                  <TableCell align="right">Occurrences</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {s.top_rules.map((r) => (
                  <TableRow key={r.rule_id}>
                    <TableCell>
                      {r.title}
                      <Typography variant="caption" display="block" color="text.secondary">
                        {r.rule_id} · {r.category}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <SeverityChip severity={r.severity} />
                    </TableCell>
                    <TableCell align="right">{r.objects}</TableCell>
                    <TableCell align="right">{r.occurrences}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card variant="outlined" sx={{ flex: '1 1 300px' }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Objects by type
            </Typography>
            <Bars data={types} total={totalObjects} />
            <Typography variant="h6" gutterBottom sx={{ mt: 2 }}>
              Findings by category
            </Typography>
            <Bars data={categories} total={totalFindings} />
          </CardContent>
        </Card>
      </Box>
    </Box>
  );
}

// ─── Objects ──────────────────────────────────────────────────────────────────

export function ObjectsTab({ report, onOpen }: { report: DiscoveryReport; onOpen: (id: string) => void }) {
  const [type, setType] = useState('all');
  const [effort, setEffort] = useState('all');
  const [search, setSearch] = useState('');
  const types = useMemo(() => Array.from(new Set(report.objects.map((o) => o.type))).sort(), [report.objects]);
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return report.objects
      .filter((o) => type === 'all' || o.type === type)
      .filter((o) => effort === 'all' || o.effort === effort)
      .filter((o) => !q || objectLabel(o).toLowerCase().includes(q) || o.file.toLowerCase().includes(q))
      .sort((a, b) => b.effort_points - a.effort_points);
  }, [report.objects, type, effort, search]);
  const { slice, pagination } = usePaged(rows);
  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <FormControl size="small" sx={{ minWidth: 180 }}>
          <InputLabel id="obj-type">Type</InputLabel>
          <Select labelId="obj-type" label="Type" value={type} onChange={(e) => setType(e.target.value)}>
            <MenuItem value="all">All types</MenuItem>
            {types.map((t) => (
              <MenuItem key={t} value={t}>
                {t} ({report.summary.object_counts[t] ?? 0})
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel id="obj-effort">Effort</InputLabel>
          <Select labelId="obj-effort" label="Effort" value={effort} onChange={(e) => setEffort(e.target.value)}>
            <MenuItem value="all">All</MenuItem>
            {EFFORTS.map((e) => (
              <MenuItem key={e} value={e}>
                {EFFORT_LABEL[e]}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <TextField size="small" label="Search objects" value={search} onChange={(e) => setSearch(e.target.value)} sx={{ minWidth: 260 }} />
        <Typography variant="body2" color="text.secondary" sx={{ alignSelf: 'center' }} data-testid="object-count">
          {rows.length} objects
        </Typography>
      </Box>
      <Table size="small" aria-label="objects">
        <TableHead>
          <TableRow>
            <TableCell>Object</TableCell>
            <TableCell>Type</TableCell>
            <TableCell align="right">LOC</TableCell>
            <TableCell>Findings (B / H / M / L)</TableCell>
            <TableCell>Effort</TableCell>
            <TableCell align="right">Called from code</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {slice.map((o) => (
            <TableRow key={o.id} hover onClick={() => onOpen(o.id)} sx={{ cursor: 'pointer' }}>
              <TableCell>
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {objectLabel(o)}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {o.file}:{o.line}
                </Typography>
              </TableCell>
              <TableCell>
                {o.type}
                {o.subtype && o.subtype !== 'standard' && o.subtype !== 'sql' && (
                  <Typography variant="caption" display="block" color="text.secondary">
                    {o.subtype}
                  </Typography>
                )}
              </TableCell>
              <TableCell align="right">{o.lines_of_code}</TableCell>
              <TableCell>
                {o.finding_counts.blocker} / {o.finding_counts.high} / {o.finding_counts.medium} / {o.finding_counts.low}
              </TableCell>
              <TableCell>
                <EffortChip effort={o.effort} />
              </TableCell>
              <TableCell align="right">{o.called_from_code.length || ''}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {pagination}
    </Box>
  );
}

export function ObjectDetail({
  report,
  objectId,
  onOpen,
}: {
  report: DiscoveryReport;
  objectId: string;
  onOpen: (id: string) => void;
}) {
  const byId = useMemo(() => new Map(report.objects.map((o) => [o.id, o])), [report.objects]);
  const obj = byId.get(objectId);
  if (!obj) return <Typography>Object not found.</Typography>;
  const findings = report.findings.filter((f) => f.object_id === obj.id);
  const refChips = (ids: string[]) =>
    ids.length === 0 ? (
      <Typography variant="body2" color="text.secondary">
        None
      </Typography>
    ) : (
      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
        {ids.map((id) => {
          const o = byId.get(id);
          return <Chip key={id} size="small" label={o ? `${o.type}: ${objectLabel(o)}` : id} onClick={() => onOpen(id)} />;
        })}
      </Box>
    );
  return (
    <Box>
      <Typography variant="overline">{obj.type}{obj.subtype ? ` · ${obj.subtype}` : ''}</Typography>
      <Typography variant="h5" sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
        {objectLabel(obj)}
      </Typography>
      <Typography variant="body2" color="text.secondary" gutterBottom component="div">
        {obj.file}:{obj.line}–{obj.end_line} · {obj.lines_of_code} lines · effort <EffortChip effort={obj.effort} /> ({obj.effort_points}{' '}
        points)
      </Typography>
      {obj.parent && (
        <Typography variant="body2" gutterBottom>
          Parent: <code>{obj.parent}</code>
        </Typography>
      )}

      {obj.columns.length > 0 && (
        <>
          <Typography variant="h6" sx={{ mt: 2 }}>
            Columns
          </Typography>
          <Table size="small" aria-label="columns">
            <TableHead>
              <TableRow>
                <TableCell>Column</TableCell>
                <TableCell>SQL Server</TableCell>
                <TableCell>MySQL 8.0</TableCell>
                <TableCell>Note</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {obj.columns.map((c) => (
                <TableRow key={c.name}>
                  <TableCell sx={{ fontFamily: 'monospace' }}>
                    {c.name}
                    {!c.nullable && ' NOT NULL'}
                  </TableCell>
                  <TableCell sx={{ fontFamily: 'monospace' }}>
                    {c.sql_type}
                    {c.identity && ' IDENTITY'}
                    {c.default_sequence && ' (sequence default)'}
                  </TableCell>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{c.mysql_type}</TableCell>
                  <TableCell>{c.mapping_note}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}

      <Typography variant="h6" sx={{ mt: 2 }}>
        Findings ({findings.length})
      </Typography>
      {findings.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No findings: expected to convert mechanically.
        </Typography>
      )}
      {findings.map((f) => (
        <Box key={f.id} sx={{ py: 1, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <SeverityChip severity={f.severity} />
            <Typography variant="subtitle2">{f.title}</Typography>
            <Typography variant="caption" color="text.secondary">
              lines {f.lines.join(', ')}
              {f.occurrences > f.lines.length ? ` (+${f.occurrences - f.lines.length} more)` : ''}
            </Typography>
          </Box>
          {f.snippet && (
            <Typography variant="body2" sx={{ fontFamily: 'monospace', bgcolor: 'grey.100', p: 0.5, my: 0.5, overflowX: 'auto' }}>
              {f.snippet}
            </Typography>
          )}
          <Typography variant="body2" color="text.secondary">
            {f.recommendation}
          </Typography>
        </Box>
      ))}

      <Typography variant="h6" sx={{ mt: 2 }}>
        References
      </Typography>
      {refChips(obj.references)}
      <Typography variant="h6" sx={{ mt: 2 }}>
        Referenced by
      </Typography>
      {refChips(obj.referenced_by)}
      <Typography variant="h6" sx={{ mt: 2 }}>
        Called from application code
      </Typography>
      {obj.called_from_code.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Not called from the scanned code.
        </Typography>
      ) : (
        obj.called_from_code.map((loc) => (
          <Typography key={loc} variant="body2" sx={{ fontFamily: 'monospace' }}>
            {loc}
          </Typography>
        ))
      )}
    </Box>
  );
}

// ─── Findings ─────────────────────────────────────────────────────────────────

export function FindingsTab({ report, onOpen }: { report: DiscoveryReport; onOpen: (id: string) => void }) {
  const [severity, setSeverity] = useState('actionable');
  const [category, setCategory] = useState('all');
  const [source, setSource] = useState('all');
  const [search, setSearch] = useState('');
  const objects = useMemo(() => new Map(report.objects.map((o) => [o.id, o])), [report.objects]);
  const categories = useMemo(() => Array.from(new Set(report.findings.map((f) => f.category))).sort(), [report.findings]);
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const order = new Map(SEVERITIES.map((s, i) => [s, i]));
    return report.findings
      .filter((f) =>
        severity === 'all' ? true : severity === 'actionable' ? f.severity !== 'info' : f.severity === severity
      )
      .filter((f) => category === 'all' || f.category === category)
      .filter((f) => source === 'all' || f.source === source)
      .filter(
        (f) =>
          !q ||
          f.title.toLowerCase().includes(q) ||
          f.rule_id.toLowerCase().includes(q) ||
          f.object_id.toLowerCase().includes(q) ||
          f.file.toLowerCase().includes(q)
      )
      .sort((a, b) => (order.get(a.severity) ?? 9) - (order.get(b.severity) ?? 9) || a.rule_id.localeCompare(b.rule_id));
  }, [report.findings, severity, category, source, search]);
  const { slice, pagination } = usePaged(rows);
  const label = (f: DbFinding) => {
    const o = objects.get(f.object_id);
    return o ? `${o.type}: ${objectLabel(o)}` : f.object_id.replace(/^(file|sql):/, '');
  };
  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <FormControl size="small" sx={{ minWidth: 170 }}>
          <InputLabel id="f-sev">Severity</InputLabel>
          <Select labelId="f-sev" label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <MenuItem value="actionable">All except info</MenuItem>
            <MenuItem value="all">All</MenuItem>
            {SEVERITIES.map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 170 }}>
          <InputLabel id="f-cat">Category</InputLabel>
          <Select labelId="f-cat" label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
            <MenuItem value="all">All</MenuItem>
            {categories.map((c) => (
              <MenuItem key={c} value={c}>
                {c}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 160 }}>
          <InputLabel id="f-src">Source</InputLabel>
          <Select labelId="f-src" label="Source" value={source} onChange={(e) => setSource(e.target.value)}>
            <MenuItem value="all">All</MenuItem>
            <MenuItem value="database">Database</MenuItem>
            <MenuItem value="application">Application</MenuItem>
          </Select>
        </FormControl>
        <TextField size="small" label="Search findings" value={search} onChange={(e) => setSearch(e.target.value)} sx={{ minWidth: 240 }} />
        <Typography variant="body2" color="text.secondary" sx={{ alignSelf: 'center' }} data-testid="finding-count">
          {rows.length} findings
        </Typography>
      </Box>
      <Table size="small" aria-label="findings">
        <TableHead>
          <TableRow>
            <TableCell>Severity</TableCell>
            <TableCell>Finding</TableCell>
            <TableCell>Object / location</TableCell>
            <TableCell align="right">Occurrences</TableCell>
            <TableCell>Recommendation</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {slice.map((f) => (
            <TableRow key={f.id} hover>
              <TableCell>
                <SeverityChip severity={f.severity} />
              </TableCell>
              <TableCell sx={{ maxWidth: 360 }}>
                <Typography variant="body2">{f.title}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {f.rule_id} · {f.category}
                </Typography>
                {f.snippet && (
                  <Typography
                    variant="caption"
                    component="div"
                    sx={{ fontFamily: 'monospace', bgcolor: 'grey.100', p: 0.5, mt: 0.5, overflowX: 'auto', whiteSpace: 'pre' }}
                  >
                    {f.snippet}
                  </Typography>
                )}
              </TableCell>
              <TableCell>
                {objects.has(f.object_id) ? (
                  <Button size="small" sx={{ textTransform: 'none', p: 0, justifyContent: 'flex-start' }} onClick={() => onOpen(f.object_id)}>
                    {label(f)}
                  </Button>
                ) : (
                  <Typography variant="body2">{label(f)}</Typography>
                )}
                <Typography variant="caption" display="block" color="text.secondary">
                  {f.file}:{f.line}
                </Typography>
              </TableCell>
              <TableCell align="right">{f.occurrences}</TableCell>
              <TableCell sx={{ maxWidth: 420 }}>
                <Typography variant="body2" color="text.secondary">
                  {f.recommendation}
                </Typography>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {pagination}
    </Box>
  );
}

// ─── Application ──────────────────────────────────────────────────────────────

function Flag({ on, label }: { on: boolean; label: string }) {
  return on ? <Chip size="small" color="error" variant="outlined" label={label} /> : null;
}

export function ApplicationTab({ report, onOpen }: { report: DiscoveryReport; onOpen: (id: string) => void }) {
  const app = report.application;
  const [showSql, setShowSql] = useState<string | null>(null);
  if (report.summary.files_scanned.application === 0) {
    return <Alert severity="info">No application files (.cs, .config, .csproj, appsettings.json) were found in this source.</Alert>;
  }
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box>
        <Typography variant="h6" gutterBottom>
          Data-access technologies
        </Typography>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          {app.technologies.length === 0 && <Typography variant="body2">None detected.</Typography>}
          {app.technologies.map((t) => (
            <Chip key={t.name} label={`${t.name} · ${t.files} files`} />
          ))}
          {app.target_frameworks.map((t) => (
            <Chip key={`${t.file}-${t.framework}`} variant="outlined" label={`${t.framework} (${t.file.split('/').pop()})`} />
          ))}
        </Box>
      </Box>

      {app.packages.length > 0 && (
        <Box>
          <Typography variant="h6" gutterBottom>
            Packages
          </Typography>
          <Table size="small" aria-label="packages">
            <TableHead>
              <TableRow>
                <TableCell>Package</TableCell>
                <TableCell>Version</TableCell>
                <TableCell>Suggested replacement</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {app.packages.map((p) => (
                <TableRow key={p.name}>
                  <TableCell>{p.name}</TableCell>
                  <TableCell>{p.version}</TableCell>
                  <TableCell>{p.replacement}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}

      <Box>
        <Typography variant="h6" gutterBottom>
          Connection strings ({app.connection_strings.length})
        </Typography>
        <Table size="small" aria-label="connection strings">
          <TableHead>
            <TableRow>
              <TableCell>Name / location</TableCell>
              <TableCell>Database</TableCell>
              <TableCell>Issues</TableCell>
              <TableCell>Connection string (passwords redacted)</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {app.connection_strings.map((c) => (
              <TableRow key={`${c.file}:${c.line}`}>
                <TableCell>
                  {c.name || '(unnamed)'}
                  <Typography variant="caption" display="block" color="text.secondary">
                    {c.file}:{c.line}
                  </Typography>
                </TableCell>
                <TableCell>{c.database}</TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                    <Flag on={c.windows_auth} label="Windows auth" />
                    <Flag on={c.mars} label="MARS" />
                    <Flag on={c.password_present} label="Password in config" />
                  </Box>
                </TableCell>
                <TableCell sx={{ fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-all' }}>{c.connection_string}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>

      <Box>
        <Typography variant="h6" gutterBottom>
          Stored-procedure calls ({app.stored_procedure_calls.length})
        </Typography>
        <Table size="small" aria-label="procedure calls">
          <TableHead>
            <TableRow>
              <TableCell>Procedure</TableCell>
              <TableCell>Location</TableCell>
              <TableCell>Status</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {app.stored_procedure_calls.map((c) => (
              <TableRow key={`${c.file}:${c.line}:${c.procedure}`}>
                <TableCell sx={{ fontFamily: 'monospace' }}>
                  {c.object_id ? (
                    <Button size="small" sx={{ textTransform: 'none', p: 0 }} onClick={() => onOpen(c.object_id as string)}>
                      {c.procedure}
                    </Button>
                  ) : (
                    c.procedure
                  )}
                </TableCell>
                <TableCell>
                  {c.file}:{c.line}
                </TableCell>
                <TableCell>
                  {c.resolved ? (
                    <Chip size="small" color="success" variant="outlined" label="found in scripts" />
                  ) : (
                    <Chip size="small" color="warning" variant="outlined" label="not in scripts" />
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>

      <Box>
        <Typography variant="h6" gutterBottom>
          Embedded SQL ({app.embedded_sql.length})
        </Typography>
        <Table size="small" aria-label="embedded sql">
          <TableHead>
            <TableRow>
              <TableCell>Location</TableCell>
              <TableCell>Rules</TableCell>
              <TableCell>SQL</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {app.embedded_sql.map((e) => (
              <TableRow key={e.id}>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>
                  {e.file.split('/').pop()}:{e.line}
                  <Box sx={{ mt: 0.5 }}>
                    <Flag on={e.dynamic} label="dynamic" />
                  </Box>
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                    {e.rule_ids.length === 0 && <Typography variant="caption">none</Typography>}
                    {e.rule_ids.map((r) => (
                      <Chip key={r} size="small" label={r} />
                    ))}
                  </Box>
                </TableCell>
                <TableCell sx={{ maxWidth: 560 }}>
                  <Typography
                    variant="caption"
                    component="pre"
                    sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', m: 0, cursor: 'pointer' }}
                    onClick={() => setShowSql(showSql === e.id ? null : e.id)}
                  >
                    {showSql === e.id ? e.sql.trim() : `${e.sql.trim().slice(0, 140)}${e.sql.trim().length > 140 ? ' …' : ''}`}
                  </Typography>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}

// ─── Type mapping ─────────────────────────────────────────────────────────────

export function TypeMappingTab({ report }: { report: DiscoveryReport }) {
  return (
    <Table size="small" aria-label="type mapping">
      <TableHead>
        <TableRow>
          <TableCell>SQL Server type</TableCell>
          <TableCell>MySQL 8.0 type</TableCell>
          <TableCell align="right">Columns</TableCell>
          <TableCell>Note</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {report.type_mappings.map((t) => (
          <TableRow key={t.sql_server_type}>
            <TableCell sx={{ fontFamily: 'monospace' }}>{t.sql_server_type}</TableCell>
            <TableCell sx={{ fontFamily: 'monospace' }}>{t.mysql_type}</TableCell>
            <TableCell align="right">{t.column_count}</TableCell>
            <TableCell>{t.note}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function DatabaseDiscoveryResults() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<DiscoveryReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [openObject, setOpenObject] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setReport(null);
    setError(null);
    getDbDiscovery(id)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        setError(status === 404 ? 'Discovery not found.' : err instanceof Error ? err.message : 'Failed to load discovery');
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) {
    return (
      <Box>
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
        <Button startIcon={<BackIcon />} onClick={() => navigate('/database-discovery')}>
          Back to Database Discovery
        </Button>
      </Box>
    );
  }
  if (!report) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  const source = report.source_url || 'ZIP upload';
  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <Box>
          <Button size="small" startIcon={<BackIcon />} onClick={() => navigate('/database-discovery')} sx={{ mb: 1 }}>
            Database Discovery
          </Button>
          <Typography variant="h4">SQL Server → {report.target}</Typography>
          <Typography variant="body2" color="text.secondary">
            {source}
            {report.subpath && ` · ${report.subpath}`}
            {report.branch && ` · ${report.branch}`} · {report.discovery_id} · {report.completed_at}
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={() => {
              setDownloadError(null);
              downloadDbDiscoveryMarkdown(report.discovery_id).catch((err: unknown) =>
                setDownloadError(err instanceof Error ? err.message : 'Download failed')
              );
            }}
          >
            Markdown report
          </Button>
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={() =>
              saveBlob(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }), `${report.discovery_id}.json`)
            }
          >
            JSON
          </Button>
          <Button
            variant="contained"
            color="secondary"
            endIcon={<ForwardIcon />}
            onClick={() => navigate(`/database-discovery/${report.discovery_id}/migration`)}
          >
            Continue to migration
          </Button>
        </Box>
      </Box>
      {downloadError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setDownloadError(null)}>
          {downloadError}
        </Alert>
      )}

      <Tabs value={tab} onChange={(_, v: number) => setTab(v)} sx={{ mb: 2 }} variant="scrollable">
        <Tab label="Overview" />
        <Tab label={`Objects (${report.objects.length})`} />
        <Tab label={`Findings (${report.findings.length})`} />
        <Tab label="Application SQL" />
        <Tab label="Type Mapping" />
      </Tabs>

      {tab === 0 && <OverviewTab report={report} />}
      {tab === 1 && <ObjectsTab report={report} onOpen={setOpenObject} />}
      {tab === 2 && <FindingsTab report={report} onOpen={setOpenObject} />}
      {tab === 3 && <ApplicationTab report={report} onOpen={setOpenObject} />}
      {tab === 4 && <TypeMappingTab report={report} />}

      <Drawer anchor="right" open={openObject !== null} onClose={() => setOpenObject(null)} PaperProps={{ sx: { width: { xs: '100%', md: 720 } } }}>
        <Box sx={{ p: 3 }}>
          <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
            <IconButton aria-label="close object detail" onClick={() => setOpenObject(null)}>
              <CloseIcon />
            </IconButton>
          </Box>
          {openObject && <ObjectDetail report={report} objectId={openObject} onOpen={setOpenObject} />}
        </Box>
      </Drawer>
    </Box>
  );
}
