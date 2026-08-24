import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Grid,
  Card,
  CardContent,
  CardActionArea,
  Typography,
  Chip,
} from '@mui/material';
import {
  Assessment as AssessmentIcon,
  Visibility as ViewIcon,
  Analytics as AnalyticsIcon,
  Transform as TransformIcon,
} from '@mui/icons-material';
import { RecentAnalysisTable } from '../components/RecentAnalysisTable';
import { getAnalyses } from '../services/api';
import type { AnalysisListItem } from '../types';

const quickActions = [
  { key: 'new', title: 'New Analysis', description: 'Analyze a codebase from GitHub or ZIP', icon: AssessmentIcon, path: '/analysis', color: '#FF9900' },
  { key: 'view', title: 'View Results', description: 'Browse previous analysis results', icon: ViewIcon, path: '/previous', color: '#232F3E' },
  { key: 'atx', title: 'ATX Analysis', description: 'Run AWS Application Transformation assessment', icon: AnalyticsIcon, path: '/atx-analysis', color: '#146EB4' },
  { key: 'transform', title: 'ATX Transform', description: 'Modernize Java applications', icon: TransformIcon, path: '/atx-transform', color: '#1B660F' },
];

export function Dashboard() {
  const navigate = useNavigate();
  const [analyses, setAnalyses] = useState<AnalysisListItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    getAnalyses()
      .then((data) => {
        if (mounted) setAnalyses(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (mounted) setAnalyses([]);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => { mounted = false; };
  }, []);

  const completedCount = analyses.filter((a) => a.status === 'completed').length;
  const totalCount = analyses.length;

  const handleView = (analysisId: string) => {
    navigate(`/results/${analysisId}`);
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Dashboard
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        AI-powered code analysis and transformation platform
      </Typography>

      {/* Stats */}
      <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
        <Chip label={`${totalCount} Total Analyses`} variant="outlined" />
        <Chip label={`${completedCount} Completed`} color="success" variant="outlined" />
      </Box>

      {/* Quick Action Cards */}
      <Grid container spacing={2} sx={{ mb: 4 }}>
        {quickActions.map(({ key, title, description, icon: Icon, path, color }) => (
          <Grid item xs={12} sm={6} md={3} key={key}>
            <Card sx={{ height: '100%' }}>
              <CardActionArea onClick={() => navigate(path)} sx={{ height: '100%' }}>
                <CardContent sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', py: 3 }}>
                  <Icon sx={{ fontSize: 40, color, mb: 1 }} />
                  <Typography variant="h6" gutterBottom>
                    {title}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {description}
                  </Typography>
                </CardContent>
              </CardActionArea>
            </Card>
          </Grid>
        ))}
      </Grid>

      {/* Recent Analyses */}
      <Typography variant="h5" gutterBottom>
        Recent Analyses
      </Typography>
      {loading ? (
        <Typography color="text.secondary">Loading analyses...</Typography>
      ) : (
        <RecentAnalysisTable analyses={analyses} onView={handleView} />
      )}
    </Box>
  );
}
