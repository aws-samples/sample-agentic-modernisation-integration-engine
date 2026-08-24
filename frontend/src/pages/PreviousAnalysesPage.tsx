import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Paper,
  IconButton,
  Chip,
  Tooltip,
  CircularProgress,
  Alert,
} from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import DeleteIcon from '@mui/icons-material/Delete';
import { getAnalyses, deleteAnalysis } from '../services/api';
import type { AnalysisListItem } from '../types';

type SortOrder = 'asc' | 'desc';

export function PreviousAnalysesPage() {
  const navigate = useNavigate();
  const [analyses, setAnalyses] = useState<AnalysisListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadAnalyses = useCallback(async () => {
    try {
      const data = await getAnalyses();
      const items = Array.isArray(data) ? data : [];
      setAnalyses(items);
      setError(null);
    } catch {
      setError('Failed to load analyses');
      setAnalyses([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAnalyses();

    // Auto-refresh every 10 seconds
    intervalRef.current = setInterval(loadAnalyses, 10000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [loadAnalyses]);

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteAnalysis(id);
        setAnalyses((prev) => prev.filter((a) => a.analysis_id !== id));
      } catch {
        // Silently handle delete errors
      }
    },
    []
  );

  const handleView = useCallback(
    (id: string) => {
      navigate(`/results/${id}`);
    },
    [navigate]
  );

  const handleSortToggle = useCallback(() => {
    setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
  }, []);

  const sortedAnalyses = [...analyses].sort((a, b) => {
    const dateA = new Date(a.created_at).getTime();
    const dateB = new Date(b.created_at).getTime();
    return sortOrder === 'asc' ? dateA - dateB : dateB - dateA;
  });

  const getStatusColor = (status: string): 'success' | 'warning' | 'error' | 'default' => {
    switch (status) {
      case 'completed':
        return 'success';
      case 'processing':
        return 'warning';
      case 'failed':
        return 'error';
      default:
        return 'default';
    }
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Previous Analyses
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        View and manage your analysis history. Auto-refreshes every 10 seconds.
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress />
        </Box>
      ) : analyses.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">
            No analyses found. Start a new analysis from the Code Analysis page.
          </Typography>
        </Paper>
      ) : (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>ID</TableCell>
                <TableCell>Source</TableCell>
                <TableCell sortDirection={sortOrder}>
                  <TableSortLabel active direction={sortOrder} onClick={handleSortToggle}>
                    Created At
                  </TableSortLabel>
                </TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {sortedAnalyses.map((analysis) => (
                <TableRow key={analysis.analysis_id} hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>
                      {analysis.analysis_id}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={analysis.source_type}
                      size="small"
                      color={analysis.source_type === 'github' ? 'primary' : 'default'}
                      variant="outlined"
                    />
                    {analysis.source_url && (
                      <Typography variant="caption" display="block" color="text.secondary" sx={{ mt: 0.5 }}>
                        {analysis.source_url}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {new Date(analysis.created_at).toLocaleString()}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Chip label={analysis.status} size="small" color={getStatusColor(analysis.status)} />
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="View Results">
                      <IconButton size="small" onClick={() => handleView(analysis.analysis_id)} color="primary">
                        <VisibilityIcon />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Delete">
                      <IconButton size="small" onClick={() => handleDelete(analysis.analysis_id)} color="error">
                        <DeleteIcon />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
