import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  Typography,
  IconButton,
  Tooltip,
} from '@mui/material';
import { Visibility as ViewIcon } from '@mui/icons-material';
import type { AnalysisListItem } from '../types';

interface RecentAnalysisTableProps {
  analyses: AnalysisListItem[];
  onView: (analysisId: string) => void;
}

function statusColor(status: string): 'success' | 'warning' | 'error' | 'default' {
  switch (status) {
    case 'completed':
      return 'success';
    case 'running':
    case 'in_progress':
      return 'warning';
    case 'failed':
    case 'error':
      return 'error';
    default:
      return 'default';
  }
}

export function RecentAnalysisTable({ analyses, onView }: RecentAnalysisTableProps) {
  if (!Array.isArray(analyses) || analyses.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ py: 2, textAlign: 'center' }}>
        No analyses yet. Start your first analysis above.
      </Typography>
    );
  }

  return (
    <TableContainer component={Paper} sx={{ maxHeight: 400 }}>
      <Table stickyHeader size="small">
        <TableHead>
          <TableRow>
            <TableCell>Analysis ID</TableCell>
            <TableCell>Source</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Created</TableCell>
            <TableCell align="right">Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {analyses.slice(0, 10).map((item) => (
            <TableRow key={item.analysis_id} hover>
              <TableCell>
                <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                  {item.analysis_id}
                </Typography>
              </TableCell>
              <TableCell>
                <Chip
                  label={item.source_type}
                  size="small"
                  variant="outlined"
                />
              </TableCell>
              <TableCell>
                <Chip
                  label={item.status}
                  size="small"
                  color={statusColor(item.status)}
                />
              </TableCell>
              <TableCell>
                <Typography variant="body2" color="text.secondary">
                  {new Date(item.created_at).toLocaleDateString()}
                </Typography>
              </TableCell>
              <TableCell align="right">
                <Tooltip title="View results">
                  <IconButton size="small" onClick={() => onView(item.analysis_id)}>
                    <ViewIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
