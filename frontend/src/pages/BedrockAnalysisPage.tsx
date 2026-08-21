import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Typography, Tabs, Tab, Alert } from '@mui/material';
import { FileUpload } from '../components/FileUpload';
import { GitHubInput } from '../components/GitHubInput';
import { ProgressTracker } from '../components/ProgressTracker';
import { uploadZip, analyzeGithub } from '../services/api';

interface TabPanelProps {
  children: React.ReactNode;
  value: number;
  index: number;
}

function TabPanel({ children, value, index }: TabPanelProps) {
  if (value !== index) return null;
  return <Box sx={{ py: 3 }}>{children}</Box>;
}

interface BedrockAnalysisPageProps {
  onAnalysisStart?: (analysisId: string) => void;
}

export function BedrockAnalysisPage({ onAnalysisStart }: BedrockAnalysisPageProps) {
  const navigate = useNavigate();
  const [tabIndex, setTabIndex] = useState(0);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const startTracking = useCallback((id: string) => {
    setAnalysisId(id);
    setError(null);
    onAnalysisStart?.(id);
  }, [onAnalysisStart]);

  const handleFileSelect = useCallback(async (file: File) => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await uploadZip(file);
      startTracking(result.analysis_id);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Upload failed';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }, [startTracking]);

  const handleGitHubSubmit = useCallback(async (repoUrl: string, branch: string, patToken?: string) => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await analyzeGithub(repoUrl, branch, patToken);
      startTracking(result.analysis_id);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Analysis failed';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }, [startTracking]);

  const handleComplete = useCallback((id: string) => {
    navigate(`/results/${id}`);
  }, [navigate]);

  const handleFailed = useCallback((_id: string, message: string) => {
    setError(message);
    setAnalysisId(null);
  }, []);

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Code Analysis
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Upload a ZIP file or provide a GitHub repository URL to analyze your codebase.
      </Typography>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {analysisId ? (
        <ProgressTracker
          analysisId={analysisId}
          onComplete={handleComplete}
          onFailed={handleFailed}
        />
      ) : (
        <>
          <Tabs value={tabIndex} onChange={(_, v: number) => setTabIndex(v)} sx={{ mb: 1 }}>
            <Tab label="GitHub" />
            <Tab label="ZIP Upload" />
          </Tabs>

          <TabPanel value={tabIndex} index={0}>
            <GitHubInput onSubmit={handleGitHubSubmit} loading={submitting} />
          </TabPanel>

          <TabPanel value={tabIndex} index={1}>
            <FileUpload onFileSelect={handleFileSelect} />
            {submitting && (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                Uploading...
              </Typography>
            )}
          </TabPanel>
        </>
      )}
    </Box>
  );
}
