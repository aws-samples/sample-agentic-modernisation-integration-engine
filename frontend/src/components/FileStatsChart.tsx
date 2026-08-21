import { Box, Typography } from '@mui/material';
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import type { FileTypeStat } from '../types';

// AWS-inspired color palette
const AWS_COLORS = [
  '#232F3E', // AWS Dark
  '#FF9900', // AWS Orange
  '#1A73E8', // Blue
  '#00A1C9', // Teal
  '#7B61FF', // Purple
  '#E63946', // Red
  '#2E7D32', // Green
  '#F4A261', // Light Orange
  '#6C757D', // Gray
  '#264653', // Dark Teal
];

interface FileStatsChartProps {
  data: FileTypeStat[];
}

export function FileStatsChart({ data }: FileStatsChartProps) {
  if (!Array.isArray(data) || data.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ p: 2 }}>
        No file statistics available.
      </Typography>
    );
  }

  const pieData = data.map((stat) => ({
    name: stat.extension || 'unknown',
    value: stat.count,
  }));

  const barData = data.map((stat) => ({
    name: stat.extension || 'unknown',
    lines: stat.total_lines,
  }));

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box>
        <Typography variant="h6" sx={{ mb: 1 }}>
          Language Distribution
        </Typography>
        <ResponsiveContainer width="100%" height={300}>
          <PieChart>
            <Pie
              data={pieData}
              cx="50%"
              cy="50%"
              outerRadius={100}
              dataKey="value"
              label={({ name, percent }) =>
                `${name} (${(percent * 100).toFixed(0)}%)`
              }
            >
              {pieData.map((_entry, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={AWS_COLORS[index % AWS_COLORS.length]}
                />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </Box>

      <Box>
        <Typography variant="h6" sx={{ mb: 1 }}>
          Lines per Language
        </Typography>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={barData}>
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip formatter={(value: number) => value.toLocaleString()} />
            <Bar dataKey="lines" name="Lines of Code">
              {barData.map((_entry, index) => (
                <Cell
                  key={`bar-${index}`}
                  fill={AWS_COLORS[index % AWS_COLORS.length]}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Box>
    </Box>
  );
}
