import { useState, useMemo } from 'react';
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Chip,
  TextField,
  InputAdornment,
  IconButton,
  Collapse,
  Typography,
  Paper,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import type { Dependency } from '../types';

interface DependencyViewerProps {
  data: Dependency[];
}

type SortField = 'name' | 'version' | 'ecosystem' | 'vulnerabilities';
type SortDirection = 'asc' | 'desc';

function DependencyRow({ dep }: { dep: Dependency }) {
  const [open, setOpen] = useState(false);
  const vulnCount = dep.vulnerabilities?.length ?? 0;

  return (
    <>
      <TableRow hover sx={{ '& > *': { borderBottom: 'unset' } }}>
        <TableCell>
          <IconButton
            aria-label="expand row"
            size="small"
            onClick={() => setOpen(!open)}
          >
            {open ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
          </IconButton>
        </TableCell>
        <TableCell component="th" scope="row">
          {dep.name}
        </TableCell>
        <TableCell>
          <Typography fontFamily="monospace" fontSize="0.875rem">
            {dep.version}
          </Typography>
        </TableCell>
        <TableCell>
          <Chip
            label={dep.ecosystem}
            size="small"
            variant="outlined"
            color="primary"
          />
        </TableCell>
        <TableCell>
          {vulnCount > 0 ? (
            <Chip
              label={`${vulnCount} vuln${vulnCount > 1 ? 's' : ''}`}
              size="small"
              color="error"
            />
          ) : (
            <Chip label="None" size="small" color="success" variant="outlined" />
          )}
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell style={{ paddingBottom: 0, paddingTop: 0 }} colSpan={5}>
          <Collapse in={open} timeout="auto" unmountOnExit>
            <Box sx={{ m: 1, p: 1, bgcolor: '#f5f5f5', borderRadius: 1 }}>
              <Typography variant="subtitle2" gutterBottom>
                Details
              </Typography>
              <Typography variant="body2">
                <strong>Source file:</strong> {dep.source_file}
              </Typography>
              <Typography variant="body2">
                <strong>Ecosystem:</strong> {dep.ecosystem}
              </Typography>
              {vulnCount > 0 && (
                <Box sx={{ mt: 1 }}>
                  <Typography variant="body2" fontWeight={600}>
                    Vulnerabilities:
                  </Typography>
                  {dep.vulnerabilities?.map((vuln, idx) => (
                    <Chip
                      key={idx}
                      label={vuln}
                      size="small"
                      color="error"
                      variant="outlined"
                      sx={{ mr: 0.5, mt: 0.5 }}
                    />
                  ))}
                </Box>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

export function DependencyViewer({ data }: DependencyViewerProps) {
  const [sortField, setSortField] = useState<SortField>('name');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [filter, setFilter] = useState('');

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const filteredAndSorted = useMemo(() => {
    if (!Array.isArray(data)) return [];

    let filtered = data;
    if (filter) {
      const lower = filter.toLowerCase();
      filtered = data.filter(
        (dep) =>
          dep.name.toLowerCase().includes(lower) ||
          dep.ecosystem.toLowerCase().includes(lower) ||
          dep.version.toLowerCase().includes(lower)
      );
    }

    return [...filtered].sort((a, b) => {
      const dir = sortDirection === 'asc' ? 1 : -1;
      switch (sortField) {
        case 'name':
          return dir * a.name.localeCompare(b.name);
        case 'version':
          return dir * a.version.localeCompare(b.version);
        case 'ecosystem':
          return dir * a.ecosystem.localeCompare(b.ecosystem);
        case 'vulnerabilities':
          return dir * ((a.vulnerabilities?.length ?? 0) - (b.vulnerabilities?.length ?? 0));
        default:
          return 0;
      }
    });
  }, [data, filter, sortField, sortDirection]);

  if (!Array.isArray(data) || data.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ p: 2 }}>
        No dependencies available.
      </Typography>
    );
  }

  return (
    <Box>
      <Box sx={{ mb: 2 }}>
        <TextField
          size="small"
          placeholder="Filter dependencies..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />
      </Box>
      <TableContainer component={Paper} sx={{ maxHeight: 500 }}>
        <Table stickyHeader size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 48 }} />
              <TableCell>
                <TableSortLabel
                  active={sortField === 'name'}
                  direction={sortField === 'name' ? sortDirection : 'asc'}
                  onClick={() => handleSort('name')}
                >
                  Name
                </TableSortLabel>
              </TableCell>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'version'}
                  direction={sortField === 'version' ? sortDirection : 'asc'}
                  onClick={() => handleSort('version')}
                >
                  Version
                </TableSortLabel>
              </TableCell>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'ecosystem'}
                  direction={sortField === 'ecosystem' ? sortDirection : 'asc'}
                  onClick={() => handleSort('ecosystem')}
                >
                  Ecosystem
                </TableSortLabel>
              </TableCell>
              <TableCell>
                <TableSortLabel
                  active={sortField === 'vulnerabilities'}
                  direction={sortField === 'vulnerabilities' ? sortDirection : 'asc'}
                  onClick={() => handleSort('vulnerabilities')}
                >
                  Vulnerabilities
                </TableSortLabel>
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {filteredAndSorted.map((dep, index) => (
              <DependencyRow key={`${dep.name}-${index}`} dep={dep} />
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  );
}
