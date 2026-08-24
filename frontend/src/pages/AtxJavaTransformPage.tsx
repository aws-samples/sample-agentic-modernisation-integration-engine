import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Chip,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  IconButton,
  Tooltip,
} from '@mui/material';
import { PlayArrow, Difference, Terminal } from '@mui/icons-material';
import { AgentLogViewer } from '../components/AgentLogViewer';
import {
  startTransformation,
  getTransformationHistory,
  getTransformations,
  streamTransformConversation,
  type TransformRecord,
} from '../services/api';
import type { SSEEvent, TransformationDefinition } from '../types';

/**
 * The value to submit as `transformation_type` for a definition.
 *
 * `name` is a display label ("Java Version Upgrade"); the ATX CLI needs the identifier
 * ("AWS/java-version-upgrade") because it is sent on as the `resource` parameter, where
 * spaces fail the service-side pattern. The agent resolves this into
 * `atx_definition_name`; the fallback to `id` covers AWS-managed records only, since a
 * custom record's `id` is a local uuid the CLI does not know.
 */
function atxDefinitionName(t: TransformationDefinition): string | null {
  if (t.atx_definition_name) return t.atx_definition_name;
  return t.type === 'custom' ? null : (t.id ?? null);
}

export function AtxJavaTransformPage() {
  const navigate = useNavigate();
  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('main');
  const [transformationType, setTransformationType] = useState('');
  const [transformations, setTransformations] = useState<TransformationDefinition[]>([]);
  const [history, setHistory] = useState<TransformRecord[]>([]);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [activeController, setActiveController] = useState<AbortController | null>(null);

  // Load transformations and history on mount
  useEffect(() => {
    getTransformations()
      .then((data) => {
        const normalized = Array.isArray(data) ? data : [];
        setTransformations(normalized);
      })
      .catch(() => setTransformations([]));

    getTransformationHistory()
      .then((data) => {
        const normalized = Array.isArray(data) ? data : [];
        setHistory(normalized);
      })
      .catch(() => setHistory([]));
  }, []);

  const handleStart = useCallback(async () => {
    if (!repoUrl.trim() || !transformationType) return;

    setEvents([]);
    setIsRunning(true);

    try {
      const result = await startTransformation(repoUrl.trim(), branch, transformationType);

      // Start streaming the conversation logs
      const controller = streamTransformConversation(result.repo_id, (event: SSEEvent) => {
        setEvents((prev) => [...prev, event]);

        if (event.type === 'complete' || event.type === 'error') {
          setIsRunning(false);
          // Refresh history
          getTransformationHistory()
            .then((data) => {
              const normalized = Array.isArray(data) ? data : [];
              setHistory(normalized);
            })
            .catch(() => {});
        }
      });

      setActiveController(controller);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to start transformation';
      setEvents((prev) => [...prev, { type: 'error', message }]);
      setIsRunning(false);
    }
  }, [repoUrl, branch, transformationType]);

  const replayConsole = useCallback(
    (record: TransformRecord) => {
      // Abort any current stream
      if (activeController) {
        activeController.abort();
      }

      setEvents([]);
      setIsRunning(true);

      const controller = streamTransformConversation(record.repo_id, (event: SSEEvent) => {
        setEvents((prev) => [...prev, event]);

        if (event.type === 'complete' || event.type === 'error') {
          setIsRunning(false);
        }
      });

      setActiveController(controller);
    },
    [activeController]
  );

  /**
   * A finished transformation's point is its output, so the row leads to the results
   * page — changed files plus the download of the whole transformed tree. Without
   * this the page at `/transform-results/:id` was routed but unreachable: nothing in
   * the app navigated to it.
   *
   * Records that have not completed have no diff to show, so they keep replaying the
   * console; the console remains one click away for completed ones too.
   */
  const handleHistoryClick = useCallback(
    (record: TransformRecord) => {
      if (record.status === 'completed') {
        navigate(`/transform-results/${record.repo_id}`);
        return;
      }
      replayConsole(record);
    },
    [navigate, replayConsole]
  );

  return (
    <Box sx={{ display: 'flex', height: 'calc(100vh - 64px)', gap: 2 }}>
      {/* Sidebar - History */}
      <Paper sx={{ width: 280, minWidth: 280, overflow: 'auto', p: 1 }}>
        <Typography variant="subtitle2" sx={{ px: 1, py: 1 }}>
          Transformation History
        </Typography>
        <List dense>
          {history.map((record) => {
            const isCompleted = record.status === 'completed';
            return (
              <ListItem
                key={record.repo_id}
                disablePadding
                secondaryAction={
                  isCompleted ? (
                    <Tooltip title="Replay transformation console">
                      <IconButton
                        edge="end"
                        size="small"
                        aria-label={`Replay console for ${record.repo_id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          replayConsole(record);
                        }}
                      >
                        <Terminal fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  ) : null
                }
              >
                <ListItemButton
                  onClick={() => handleHistoryClick(record)}
                  aria-label={
                    isCompleted
                      ? `View transform results for ${record.repo_id}`
                      : `Replay console for ${record.repo_id}`
                  }
                >
                  <ListItemText
                    primary={record.repo_url ? record.repo_url.split('/').pop() : record.repo_id.slice(0, 12)}
                    // The secondary slot holds a Chip (a <div>), which is invalid
                    // inside Typography's default <p>.
                    secondaryTypographyProps={{ component: 'div' }}
                    secondary={
                      <Box component="span" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                        <Chip
                          label={record.status}
                          size="small"
                          color={
                            isCompleted
                              ? 'success'
                              : record.status === 'running'
                                ? 'info'
                                : record.status === 'failed'
                                  ? 'error'
                                  : 'default'
                          }
                          sx={{ height: 18, fontSize: '0.65rem' }}
                        />
                        <Typography variant="caption">
                          {new Date(record.created_at).toLocaleDateString()}
                        </Typography>
                      </Box>
                    }
                  />
                  {isCompleted && (
                    <Difference fontSize="small" sx={{ color: 'text.secondary', ml: 0.5 }} />
                  )}
                </ListItemButton>
              </ListItem>
            );
          })}
          {history.length === 0 && (
            <Typography variant="body2" sx={{ px: 2, py: 1, color: 'text.secondary' }}>
              No transformations yet
            </Typography>
          )}
        </List>
      </Paper>

      {/* Main Content */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Transform Form */}
        <Paper sx={{ p: 2, mb: 2 }}>
          <Typography variant="h6" gutterBottom>
            ATX Transform
          </Typography>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <TextField
              label="Repository URL"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              sx={{ flex: 2, minWidth: 250 }}
              size="small"
              placeholder="https://github.com/org/repo"
              disabled={isRunning}
            />
            <TextField
              label="Branch"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              sx={{ flex: 1, minWidth: 120 }}
              size="small"
              disabled={isRunning}
            />
            <FormControl sx={{ flex: 1, minWidth: 200 }} size="small">
              {/* labelId/id wire the label to the listbox trigger, so the control has an
                  accessible name instead of an unassociated floating label. */}
              <InputLabel id="transformation-type-label">Transformation Type</InputLabel>
              <Select
                labelId="transformation-type-label"
                id="transformation-type"
                value={transformationType}
                onChange={(e) => setTransformationType(e.target.value)}
                label="Transformation Type"
                disabled={isRunning}
              >
                {transformations.map((t) => {
                  const definitionName = atxDefinitionName(t);
                  return (
                    <MenuItem
                      key={t.id ?? definitionName ?? t.name}
                      value={definitionName ?? ''}
                      disabled={!definitionName}
                    >
                      {t.name}
                    </MenuItem>
                  );
                })}
              </Select>
            </FormControl>
            <Button
              variant="contained"
              startIcon={<PlayArrow />}
              onClick={handleStart}
              disabled={isRunning || !repoUrl.trim() || !transformationType}
              sx={{ minWidth: 120 }}
            >
              Start
            </Button>
          </Box>
        </Paper>

        {/* SSE Console */}
        <Paper sx={{ flex: 1, overflow: 'hidden', p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Transformation Console
          </Typography>
          <AgentLogViewer events={events} maxHeight={500} />
        </Paper>
      </Box>
    </Box>
  );
}
