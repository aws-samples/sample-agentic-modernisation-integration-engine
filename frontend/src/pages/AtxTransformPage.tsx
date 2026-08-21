import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Box, Typography, Button, Alert, CircularProgress } from '@mui/material';
import { Download } from '@mui/icons-material';
import { EnhancedFileComparison, type FileDiff, type DiffCategory } from '../components/EnhancedFileComparison';
import { getDiff, getDiffSummary, downloadTransformedTree } from '../services/api';

/**
 * Shape of `GET /diff-summary/{repo_id}`.
 *
 * `changed_files` is what the header wants — `total_files` counts every file the
 * comparison walked, unchanged ones included, so reporting it as "files changed"
 * overstated the change by the size of the repository.
 *
 * `source_files_changed` / `documentation_files_changed` split those changed files into
 * the two things a transformation can produce. They are optional here only so an older
 * agent build still renders; when absent the counts are derived from the diff payload.
 */
interface DiffSummaryData {
  total_files: number;
  changed_files: number;
  additions: number;
  deletions: number;
  source_files_changed?: number;
  documentation_files_changed?: number;
}

/** Mirrors the agent's `classify_category`, used only when a payload predates it. */
function fallbackCategory(filename: string, status?: string): DiffCategory {
  if (filename.split('/').includes('ATXDocumentation')) return 'documentation';
  if (status === 'added' && /\.(md|markdown)$/i.test(filename)) return 'documentation';
  return 'source';
}

export function AtxTransformPage() {
  const { id } = useParams<{ id: string }>();
  const [files, setFiles] = useState<FileDiff[]>([]);
  const [summary, setSummary] = useState<DiffSummaryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;

    setLoading(true);
    setError(null);

    Promise.all([getDiff(id), getDiffSummary(id)])
      .then(([diffData, summaryData]) => {
        // Normalize diff response into FileDiff[]
        const rawFiles = (diffData as { files?: unknown[] })?.files;
        if (Array.isArray(rawFiles)) {
          const normalized: FileDiff[] = rawFiles.map((f: unknown) => {
            const file = f as {
              filename?: string;
              status?: string;
              category?: DiffCategory;
              truncated?: boolean;
              lines?: unknown[];
            };
            const filename = file.filename ?? 'unknown';
            return {
              filename,
              status: file.status,
              category: file.category ?? fallbackCategory(filename, file.status),
              truncated: file.truncated === true,
              lines: Array.isArray(file.lines)
                ? file.lines.map((l: unknown) => {
                    const line = l as { type?: string; content?: string; old_line_number?: number; new_line_number?: number };
                    return {
                      type: (line.type as 'added' | 'removed' | 'unchanged') ?? 'unchanged',
                      content: line.content ?? '',
                      oldLineNumber: line.old_line_number,
                      newLineNumber: line.new_line_number,
                    };
                  })
                : [],
            };
          });
          setFiles(normalized);
        } else {
          setFiles([]);
        }

        const rawSummary = summaryData as DiffSummaryData | null;
        setSummary(rawSummary);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : 'Failed to load diff';
        setError(msg);
        setFiles([]);
      })
      .finally(() => setLoading(false));
  }, [id]);

  /**
   * Downloads the whole transformed tree, not just the changed files — the
   * changed-files view above is the review surface, this is the artefact.
   *
   * Enabled regardless of whether the diff is empty: a transformation can produce
   * output the diff view has nothing to show for (documentation-only runs), and the
   * tree is still worth having.
   *
   * This is the only action on the page. Publishing the result as a pull request was
   * removed from the UI; the transform agent still exposes it over HTTP.
   */
  const handleDownload = async () => {
    if (!id) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadTransformedTree(id);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed');
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '50vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  // What this run is made of. Reported from the agent's counts where available, else
  // derived from the payload — either way both numbers are stated, including zeros.
  // "0 source files changed, 32 documentation files generated" is a complete answer;
  // an undifferentiated list of 32 markdown files is not.
  const sourceChanged =
    summary?.source_files_changed ?? files.filter((file) => (file.category ?? 'source') === 'source').length;
  const documentationChanged =
    summary?.documentation_files_changed ?? files.filter((file) => file.category === 'documentation').length;
  const documentationOnly = sourceChanged === 0 && documentationChanged > 0;

  return (
    <Box sx={{ p: 2 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Box>
          <Typography variant="h5">Transform Results</Typography>
          {summary && (
            <Typography variant="body2" color="text.secondary">
              {summary.changed_files ?? 0} files changed, {summary.additions ?? 0} additions,{' '}
              {summary.deletions ?? 0} deletions
            </Typography>
          )}
          <Typography variant="body2" color="text.secondary">
            {sourceChanged} source {sourceChanged === 1 ? 'file' : 'files'} changed,{' '}
            {documentationChanged} documentation {documentationChanged === 1 ? 'file' : 'files'} generated
          </Typography>
        </Box>
        {/* Sole action, so no action group wrapper: the header's space-between puts it
            at the right on its own. It is never gated on the diff being non-empty —
            that gate belonged to the removed PR button. */}
        <Button
          variant="outlined"
          startIcon={downloading ? <CircularProgress size={16} /> : <Download />}
          onClick={handleDownload}
          disabled={downloading}
          sx={{ flexShrink: 0 }}
        >
          {downloading ? 'Preparing…' : 'Download Code'}
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {downloadError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setDownloadError(null)}>
          {downloadError}
        </Alert>
      )}

      {/* A documentation-only run is a complete result, not a missing one. Analysis-type
          definitions (comprehensive-codebase-analysis) write an ATXDocumentation/ tree and
          edit no source; saying so beats leaving the reader to wonder. */}
      {documentationOnly && !error && (
        <Alert severity="info" sx={{ mb: 2 }}>
          This transformation generated documentation and made no source code changes. All{' '}
          {documentationChanged} generated {documentationChanged === 1 ? 'file is' : 'files are'} listed
          below and can be reviewed here or downloaded with the transformed tree.
        </Alert>
      )}

      {/* Diff Viewer */}
      <EnhancedFileComparison files={files} />
    </Box>
  );
}
