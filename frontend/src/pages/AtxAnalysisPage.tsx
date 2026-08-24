import { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  List,
  ListItemButton,
  ListItemText,
  Tabs,
  Tab,
  Chip,
} from '@mui/material';
import { PlayArrow, Cancel, Description } from '@mui/icons-material';
import { AgentLogViewer } from '../components/AgentLogViewer';
import { AtxDocumentationPanel } from '../components/AtxDocumentationPanel';
import {
  startAtxAnalysis,
  streamAtxConversation,
  cancelAtxAnalysis,
  getAtxConversations,
  type AtxConversation,
} from '../services/api';
import type { SSEEvent } from '../types';

// Survives a page refresh so the console can be restored (Defect 1).
const LAST_CONVERSATION_KEY = 'atx_analysis_last_conversation';

export function AtxAnalysisPage() {
  const [repoUrl, setRepoUrl] = useState('');
  const [conversations, setConversations] = useState<AtxConversation[]>([]);
  const [selectedConversation, setSelectedConversation] = useState<string | null>(null);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [activeController, setActiveController] = useState<AbortController | null>(null);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState(0);
  const [streamError, setStreamError] = useState<string | null>(null);

  const refreshConversations = useCallback(async (): Promise<AtxConversation[]> => {
    try {
      const data = await getAtxConversations();
      const normalized = Array.isArray(data) ? data : [];
      setConversations(normalized);
      return normalized;
    } catch {
      setConversations([]);
      return [];
    }
  }, []);

  /**
   * Attach to a conversation's event stream: replayed events first, then live
   * output if it is still running. Works for finished conversations too — they
   * replay only.
   */
  const attachToConversation = useCallback(
    (conversationId: string, expectRunning: boolean) => {
      setEvents([]);
      setStreamError(null);
      setCurrentConversationId(conversationId);
      setIsRunning(expectRunning);

      let received = 0;
      const controller = streamAtxConversation(conversationId, (event: SSEEvent) => {
        if (event.type === 'error' && received === 0) {
          // The stream itself is unavailable (404, network, agent down). Say so
          // instead of leaving an empty console spinning forever.
          setStreamError(
            `Could not attach to conversation ${conversationId}: ${event.message}. ` +
              'Its event history may no longer be available.'
          );
          setIsRunning(false);
          return;
        }
        received += 1;
        setEvents((prev) => [...prev, event]);

        if (event.type === 'complete' || event.type === 'error' || event.type === 'cancelled') {
          setIsRunning(false);
          void refreshConversations();
        }
      });

      setActiveController(controller);
    },
    [refreshConversations]
  );

  // Load conversations on mount and restore the previously viewed conversation
  // so a refresh does not lose a running analysis.
  useEffect(() => {
    let cancelled = false;

    void refreshConversations().then((list) => {
      if (cancelled || list.length === 0) return;

      const remembered = localStorage.getItem(LAST_CONVERSATION_KEY);
      const target =
        list.find((c) => c.conversation_id === remembered) ??
        list.find((c) => c.status === 'running');
      if (!target) return;

      setSelectedConversation(target.conversation_id);
      setActiveTab(0);
      attachToConversation(target.conversation_id, target.status === 'running');
    });

    return () => {
      cancelled = true;
    };
    // Mount only: re-running this would fight with user selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleStart = useCallback(() => {
    if (!repoUrl.trim()) return;

    setEvents([]);
    setStreamError(null);
    setIsRunning(true);
    setCurrentConversationId(null);
    setActiveTab(0);

    const controller = startAtxAnalysis(repoUrl.trim(), 'code-assessment', (event: SSEEvent) => {
      setEvents((prev) => [...prev, event]);

      if (event.type === 'init') {
        setCurrentConversationId(event.conversation_id);
        setSelectedConversation(event.conversation_id);
        localStorage.setItem(LAST_CONVERSATION_KEY, event.conversation_id);
        void refreshConversations();
      }

      if (event.type === 'complete' || event.type === 'error' || event.type === 'cancelled') {
        setIsRunning(false);
        void refreshConversations();
      }
    });

    setActiveController(controller);
  }, [repoUrl, refreshConversations]);

  const handleCancel = useCallback(async () => {
    if (activeController) {
      activeController.abort();
    }
    if (currentConversationId) {
      try {
        await cancelAtxAnalysis(currentConversationId);
      } catch {
        // Best-effort cancel
      }
    }
    setIsRunning(false);
  }, [activeController, currentConversationId]);

  const handleConversationSelect = useCallback(
    (conversationId: string) => {
      if (conversationId === selectedConversation) return;

      // Detach from whatever we were watching before re-attaching.
      activeController?.abort();

      setSelectedConversation(conversationId);
      localStorage.setItem(LAST_CONVERSATION_KEY, conversationId);
      setActiveTab(0);

      const conversation = conversations.find((c) => c.conversation_id === conversationId);
      attachToConversation(conversationId, conversation?.status === 'running');
    },
    [selectedConversation, activeController, conversations, attachToConversation]
  );

  return (
    <Box sx={{ display: 'flex', height: 'calc(100vh - 64px)', gap: 2 }}>
      {/* Sidebar - Conversations */}
      <Paper sx={{ width: 280, minWidth: 280, overflow: 'auto', p: 1 }}>
        <Typography variant="subtitle2" sx={{ px: 1, py: 1 }}>
          Conversations
        </Typography>
        <List dense>
          {conversations.map((conv) => (
            <ListItemButton
              key={conv.conversation_id}
              selected={selectedConversation === conv.conversation_id}
              onClick={() => handleConversationSelect(conv.conversation_id)}
            >
              <ListItemText
                primary={conv.conversation_id.slice(0, 12) + '...'}
                // The secondary slot holds a Chip (a <div>), which is invalid
                // inside the default <p>.
                secondaryTypographyProps={{ component: 'span' }}
                secondary={
                  <Box component="span" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Chip
                      label={conv.status}
                      size="small"
                      color={conv.status === 'completed' ? 'success' : conv.status === 'running' ? 'info' : 'default'}
                      sx={{ height: 18, fontSize: '0.65rem' }}
                    />
                    <Typography variant="caption">{new Date(conv.created_at).toLocaleDateString()}</Typography>
                  </Box>
                }
              />
            </ListItemButton>
          ))}
          {conversations.length === 0 && (
            <Typography variant="body2" sx={{ px: 2, py: 1, color: 'text.secondary' }}>
              No conversations yet
            </Typography>
          )}
        </List>
      </Paper>

      {/* Main Content */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Analysis Form */}
        <Paper sx={{ p: 2, mb: 2 }}>
          <Typography variant="h6" gutterBottom>
            ATX Code Analysis
          </Typography>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-end' }}>
            <TextField
              label="Repository URL"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              fullWidth
              size="small"
              placeholder="https://github.com/org/repo"
              disabled={isRunning}
            />
            <Button
              variant="contained"
              startIcon={<PlayArrow />}
              onClick={handleStart}
              disabled={isRunning || !repoUrl.trim()}
              sx={{ minWidth: 120 }}
            >
              Start
            </Button>
            {isRunning && (
              <Button
                variant="outlined"
                color="error"
                startIcon={<Cancel />}
                onClick={handleCancel}
                sx={{ minWidth: 120 }}
              >
                Cancel
              </Button>
            )}
          </Box>
        </Paper>

        {/* Tabs: Console / Docs */}
        <Paper sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <Tabs value={activeTab} onChange={(_, v: number) => setActiveTab(v)} sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
            <Tab label="Console" />
            <Tab label="Documentation" icon={<Description />} iconPosition="start" />
          </Tabs>

          <Box sx={{ flex: 1, overflow: 'auto', p: 2 }}>
            {activeTab === 0 && (
              <AgentLogViewer
                events={events}
                maxHeight={600}
                emptyMessage={
                  streamError ??
                  (isRunning
                    ? 'Analysis starting — waiting for the first agent output...'
                    : selectedConversation
                      ? 'No recorded output for this conversation.'
                      : 'Start an analysis or select a conversation to view its output.')
                }
              />
            )}
            {activeTab === 1 && <AtxDocumentationPanel conversationId={selectedConversation} />}
          </Box>
        </Paper>
      </Box>
    </Box>
  );
}
