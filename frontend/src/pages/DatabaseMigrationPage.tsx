import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Box, Button, CircularProgress, Step, StepLabel, Stepper, Tab, Tabs, Typography } from '@mui/material';
import { ArrowBack as BackIcon } from '@mui/icons-material';
import { getDbDiscovery } from '../services/api';
import { DbConversionPanel } from '../components/DbConversionPanel';
import { DbValidationPanel } from '../components/DbValidationPanel';
import { DbDmsPanel } from '../components/DbDmsPanel';
import type { DiscoveryReport } from '../types/dbDiscovery';

const STAGES = ['Discovery', 'Conversion', 'Validation', 'DMS'];

/**
 * Migration steps that act on a finished discovery: convert and review code, validate data, and
 * generate DMS artifacts. Kept apart from the read-only discovery report so that running a
 * discovery never looks like it converts anything.
 */
export function DatabaseMigrationPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<DiscoveryReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await getDbDiscovery(id);
        if (!cancelled) setReport(r);
      } catch (err: unknown) {
        if (cancelled) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        setError(status === 404 ? 'Discovery not found.' : err instanceof Error ? err.message : 'Failed to load discovery');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) {
    return (
      <Box>
        <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>
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

  return (
    <Box>
      <Button size="small" startIcon={<BackIcon />} onClick={() => navigate(`/database-discovery/${report.discovery_id}`)} sx={{ mb: 1 }}>
        Discovery report
      </Button>
      <Typography variant="h4">Migration: SQL Server → {report.target}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Based on discovery {report.discovery_id} · {report.source_url || 'ZIP upload'}
        {report.subpath && ` · ${report.subpath}`} · {report.completed_at}
      </Typography>

      <Stepper activeStep={tab + 1} sx={{ mb: 3, maxWidth: 720 }}>
        {STAGES.map((s) => (
          <Step key={s} completed={s === 'Discovery'}>
            <StepLabel>{s}</StepLabel>
          </Step>
        ))}
      </Stepper>

      <Tabs value={tab} onChange={(_, v: number) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Conversion" />
        <Tab label="Validation" />
        <Tab label="DMS" />
      </Tabs>

      {tab === 0 && <DbConversionPanel report={report} />}
      {tab === 1 && <DbValidationPanel report={report} />}
      {tab === 2 && <DbDmsPanel report={report} />}
    </Box>
  );
}
