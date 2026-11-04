import { useMemo, useState } from 'react';
import { Alert, Box, Button, Card, CardContent, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { Download as DownloadIcon } from '@mui/icons-material';
import { downloadDbDmsArtifacts } from '../services/api';
import type { DiscoveryReport } from '../types/dbDiscovery';

const FILES: [string, string][] = [
  ['README.md', 'Order of work, CDC prerequisites, and the tables and columns that need a decision.'],
  ['schema.sql', 'Target MySQL DDL (deterministic). Apply it, plus approved conversions, before the task runs.'],
  ['table-mappings.json', 'One selection rule per schema; memory-optimised tables excluded.'],
  ['task-settings.json', 'TargetTablePrepMode DO_NOTHING, LOB mode from discovered types, row-level validation.'],
  ['cloudformation-dms.yaml', 'Replication instance, endpoints (Secrets Manager), full-load + CDC task, optional Schema Conversion project.'],
];

export function DbDmsPanel({ report }: { report: DiscoveryReport }) {
  const [error, setError] = useState<string | null>(null);
  const prereqs = useMemo(() => {
    const tables = report.objects.filter((o) => o.type === 'table');
    return {
      tables: tables.length,
      noPk: tables.filter((t) => !(t.primary_key ?? []).length).length,
      memopt: tables.filter((t) => t.subtype.includes('memory_optimized')).length,
      temporal: tables.filter((t) => t.subtype.includes('temporal')).length,
    };
  }, [report.objects]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Alert severity="info">
        These artifacts are generated for review and deployment by you. AMIE never creates AWS resources; deploying the
        CloudFormation template creates billable DMS resources.
      </Alert>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        {[
          ['Tables in scope', prereqs.tables],
          ['Without primary key (no CDC)', prereqs.noPk],
          ['Memory-optimised (excluded)', prereqs.memopt],
          ['Temporal (history via triggers)', prereqs.temporal],
        ].map(([k, v]) => (
          <Card key={k as string} variant="outlined" sx={{ flex: '1 1 180px' }}>
            <CardContent>
              <Typography variant="body2" color="text.secondary">{k}</Typography>
              <Typography variant="h4" data-testid={`dms-${String(k).split(' ')[0].toLowerCase()}`}>{v}</Typography>
            </CardContent>
          </Card>
        ))}
      </Box>
      <Table size="small" aria-label="dms files">
        <TableHead>
          <TableRow>
            <TableCell>File</TableCell>
            <TableCell>Contents</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {FILES.map(([f, d]) => (
            <TableRow key={f}>
              <TableCell sx={{ fontFamily: 'monospace' }}>{f}</TableCell>
              <TableCell>{d}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Box>
        <Button
          variant="contained"
          color="secondary"
          startIcon={<DownloadIcon />}
          onClick={() =>
            downloadDbDmsArtifacts(report.discovery_id).catch((err: unknown) =>
              setError(err instanceof Error ? err.message : 'Download failed')
            )
          }
        >
          Download DMS artifacts (.zip)
        </Button>
      </Box>
    </Box>
  );
}
