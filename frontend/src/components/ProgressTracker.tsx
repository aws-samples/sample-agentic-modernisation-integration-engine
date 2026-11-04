import { useState, useEffect, useRef } from 'react';
import { Box, Stepper, Step, StepLabel, LinearProgress, Typography } from '@mui/material';
import { getAnalysisStatus } from '../services/api';

const STEPS = ['Initializing', 'Parsing', 'Dependencies', 'Diagrams', 'AI Enrichment'];

function stepToIndex(currentStep: string): number {
  const lower = currentStep.toLowerCase();
  if (lower.includes('pars')) return 1;
  if (lower.includes('depend')) return 2;
  if (lower.includes('diagram')) return 3;
  if (lower.includes('ai') || lower.includes('enrich')) return 4;
  return 0;
}

/** The fields the tracker reads from a status poll. */
export interface TrackedStatus {
  status: string;
  progress?: number;
  current_step?: string;
  message?: string;
}

interface ProgressTrackerProps {
  analysisId: string;
  onComplete: (analysisId: string) => void;
  onFailed: (analysisId: string, message: string) => void;
  /** Status source; defaults to the code-analysis status endpoint. */
  fetchStatus?: (id: string) => Promise<TrackedStatus>;
  /** Step labels; defaults to the code-analysis pipeline steps. */
  steps?: string[];
  /** Maps the backend's current_step to an index into `steps`. */
  stepIndexFor?: (currentStep: string) => number;
}

export function ProgressTracker({
  analysisId,
  onComplete,
  onFailed,
  fetchStatus = getAnalysisStatus,
  steps = STEPS,
  stepIndexFor = stepToIndex,
}: ProgressTrackerProps) {
  const [progress, setProgress] = useState(0);
  const [activeStep, setActiveStep] = useState(0);
  const [message, setMessage] = useState('Starting analysis...');
  const [status, setStatus] = useState('running');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    const poll = async () => {
      try {
        const data = await fetchStatus(analysisId);
        if (!mountedRef.current) return;

        setProgress(data.progress ?? 0);
        setMessage(data.message ?? data.current_step ?? '');
        setStatus(data.status);
        setActiveStep(stepIndexFor(data.current_step ?? ''));

        if (data.status === 'completed') {
          if (intervalRef.current) clearInterval(intervalRef.current);
          onComplete(analysisId);
        } else if (data.status === 'failed' || data.status === 'error') {
          if (intervalRef.current) clearInterval(intervalRef.current);
          onFailed(analysisId, data.message ?? 'Analysis failed');
        }
      } catch {
        // Ignore transient poll errors
      }
    };

    poll();
    intervalRef.current = setInterval(poll, 2000);

    return () => {
      mountedRef.current = false;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [analysisId, onComplete, onFailed, fetchStatus, stepIndexFor]);

  const isComplete = status === 'completed';
  const isFailed = status === 'failed' || status === 'error';

  return (
    <Box sx={{ width: '100%', py: 2 }}>
      <Stepper activeStep={isComplete ? steps.length : activeStep} alternativeLabel>
        {steps.map((label) => (
          <Step key={label} completed={isComplete}>
            <StepLabel
              error={isFailed && label === steps[activeStep]}
            >
              {label}
            </StepLabel>
          </Step>
        ))}
      </Stepper>

      <Box sx={{ mt: 2 }}>
        <LinearProgress
          variant="determinate"
          value={progress}
          color={isFailed ? 'error' : isComplete ? 'success' : 'secondary'}
          sx={{ height: 8, borderRadius: 4 }}
        />
        <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            {message}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {progress}%
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}
