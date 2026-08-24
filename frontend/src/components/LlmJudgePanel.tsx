import { useState, useCallback, useRef, useEffect } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  CircularProgress,
  Button,
  Grid,
  Paper,
  List,
  ListItem,
  ListItemText,
  Divider,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { streamJudge, getDocAnalysisRuns } from '../services/api';
import type { SSEEvent } from '../types';

interface ScoreCard {
  dimension: string;
  score: number;
  justification: string;
}

interface EvaluationEntry {
  timestamp: string;
  overall_score: number;
  scores: ScoreCard[];
  feedback: string;
}

interface LlmJudgePanelProps {
  analysisId: string;
}

function CircularScoreProgress({ score, size = 80 }: { score: number; size?: number }) {
  const normalizedValue = (score / 10) * 100;
  const color = score >= 7 ? 'success' : score >= 4 ? 'warning' : 'error';

  return (
    <Box sx={{ position: 'relative', display: 'inline-flex' }}>
      <CircularProgress
        variant="determinate"
        value={normalizedValue}
        size={size}
        thickness={4}
        color={color}
      />
      <Box
        sx={{
          top: 0,
          left: 0,
          bottom: 0,
          right: 0,
          position: 'absolute',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Typography variant="h6" component="span" color="text.primary">
          {score.toFixed(1)}
        </Typography>
      </Box>
    </Box>
  );
}

export function LlmJudgePanel({ analysisId }: LlmJudgePanelProps) {
  const [scores, setScores] = useState<ScoreCard[]>([]);
  const [overallScore, setOverallScore] = useState<number | null>(null);
  const [feedbackText, setFeedbackText] = useState('');
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [evaluationHistory, setEvaluationHistory] = useState<EvaluationEntry[]>([]);
  const controllerRef = useRef<AbortController | null>(null);

  const handleSSEEvent = useCallback((event: SSEEvent) => {
    switch (event.type) {
      case 'content': {
        // Try to parse structured judge output
        try {
          const parsed = JSON.parse(event.text);
          if (parsed.dimension && typeof parsed.score === 'number') {
            setScores((prev) => {
              const existing = prev.findIndex((s) => s.dimension === parsed.dimension);
              const newScore: ScoreCard = {
                dimension: parsed.dimension,
                score: parsed.score,
                justification: parsed.justification ?? '',
              };
              if (existing >= 0) {
                const updated = [...prev];
                updated[existing] = newScore;
                return updated;
              }
              return [...prev, newScore];
            });
          }
          if (typeof parsed.overall_score === 'number') {
            setOverallScore(parsed.overall_score);
          }
          if (parsed.feedback) {
            setFeedbackText(parsed.feedback);
          }
        } catch {
          // Accumulate as feedback text if not JSON
          setFeedbackText((prev) => prev + event.text);
        }
        break;
      }
      case 'complete':
        setIsEvaluating(false);
        break;
      case 'error':
        setIsEvaluating(false);
        break;
      default:
        break;
    }
  }, []);

  const startEvaluation = useCallback(() => {
    if (controllerRef.current) {
      controllerRef.current.abort();
    }
    setScores([]);
    setOverallScore(null);
    setFeedbackText('');
    setIsEvaluating(true);
    controllerRef.current = streamJudge(analysisId, handleSSEEvent);
  }, [analysisId, handleSSEEvent]);

  const loadHistory = useCallback(async () => {
    try {
      const data = await getDocAnalysisRuns(analysisId);
      const entries: EvaluationEntry[] = (Array.isArray(data) ? data : [])
        .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
        .filter((item) => 'judge_scores' in item || 'overall_score' in item)
        .map((item) => ({
          timestamp: String(item.timestamp ?? ''),
          overall_score: Number(item.overall_score ?? 0),
          scores: Array.isArray(item.judge_scores) ? (item.judge_scores as ScoreCard[]) : [],
          feedback: String(item.feedback ?? ''),
        }));
      setEvaluationHistory(entries);
    } catch {
      setEvaluationHistory([]);
    }
  }, [analysisId]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const dimensions = scores.length > 0
    ? scores
    : [
        { dimension: 'Accuracy', score: 0, justification: 'Not evaluated' },
        { dimension: 'Completeness', score: 0, justification: 'Not evaluated' },
        { dimension: 'Actionability', score: 0, justification: 'Not evaluated' },
        { dimension: 'Specificity', score: 0, justification: 'Not evaluated' },
        { dimension: 'Correctness', score: 0, justification: 'Not evaluated' },
      ];

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h5">Quality Evaluation</Typography>
        <Button
          variant="contained"
          startIcon={<RefreshIcon />}
          onClick={startEvaluation}
          disabled={isEvaluating}
          color="secondary"
        >
          {isEvaluating ? 'Evaluating...' : 'Evaluate'}
        </Button>
      </Box>

      {isEvaluating && scores.length === 0 && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <CircularProgress size={20} />
          <Typography color="text.secondary">Running LLM Judge evaluation...</Typography>
        </Box>
      )}

      {/* Overall Score */}
      {overallScore !== null && (
        <Paper sx={{ p: 3, mb: 3, textAlign: 'center' }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Overall Score
          </Typography>
          <CircularScoreProgress score={overallScore} size={100} />
        </Paper>
      )}

      {/* 5 Dimension Score Cards */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {dimensions.map((dim) => (
          <Grid item xs={12} sm={6} md={4} lg={2.4} key={dim.dimension}>
            <Card sx={{ height: '100%', textAlign: 'center' }}>
              <CardContent>
                <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                  {dim.dimension}
                </Typography>
                <CircularScoreProgress score={dim.score} size={70} />
                {dim.justification && dim.justification !== 'Not evaluated' && (
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                    {dim.justification.slice(0, 100)}
                    {dim.justification.length > 100 ? '...' : ''}
                  </Typography>
                )}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      {/* Feedback Text */}
      {feedbackText && (
        <Paper sx={{ p: 2, mb: 3 }}>
          <Typography variant="subtitle2" gutterBottom>
            Judge Feedback
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap' }}>
            {feedbackText}
          </Typography>
        </Paper>
      )}

      {/* Evaluation History */}
      {evaluationHistory.length > 0 && (
        <Box>
          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" gutterBottom>
            Evaluation History
          </Typography>
          <List>
            {evaluationHistory.map((entry) => (
              <ListItem key={entry.timestamp} divider>
                <ListItemText
                  primary={`Score: ${entry.overall_score.toFixed(1)} / 10`}
                  secondary={new Date(entry.timestamp).toLocaleString()}
                />
              </ListItem>
            ))}
          </List>
        </Box>
      )}
    </Box>
  );
}
