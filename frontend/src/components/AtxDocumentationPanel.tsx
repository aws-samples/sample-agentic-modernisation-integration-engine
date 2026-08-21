import { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Box,
  List,
  ListItemButton,
  ListItemText,
  Typography,
  CircularProgress,
  Alert,
  Paper,
  Button,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getAtxConversationDocs, getAtxFileContent, type AtxDoc } from '../services/api';
import { useMarkdownComponents, type DocNavigation } from '../utils/markdownComponents';

/** Statuses that mean the analysis can still produce documentation. */
const IN_FLIGHT_STATUSES = new Set(['running', 'queued', 'pending']);

interface AtxDocumentationPanelProps {
  /** Conversation whose documentation to show, or null if none is selected. */
  conversationId: string | null;
}

/**
 * Documentation tab for an ATX analysis.
 *
 * Lists the documents collected for the conversation and renders the selected
 * one as markdown. The empty states are deliberately distinct: "still running",
 * "produced no documentation", and "could not load" are three different facts and
 * a reader must be able to tell which one applies. Document metadata is never
 * rendered in place of document content.
 *
 * The collected set is a linked tree, not 32 unrelated files: `README.md` is an index
 * linking its siblings by relative path, and those siblings cross-link each other. Those
 * links navigate inside this panel — the documents are already loaded and addressable, so
 * a relative href resolves to a selection rather than to a URL the SPA cannot serve.
 */
export function AtxDocumentationPanel({ conversationId }: AtxDocumentationPanelProps) {
  const [docs, setDocs] = useState<AtxDoc[]>([]);
  const [conversationStatus, setConversationStatus] = useState<string>('unknown');
  const [listState, setListState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle');
  const [listError, setListError] = useState<string | null>(null);

  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [contentState, setContentState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle');
  const [contentError, setContentError] = useState<string | null>(null);

  // Documents opened by following a link, so an index link is not a one-way trip.
  const [history, setHistory] = useState<string[]>([]);
  // Anchor to scroll to once the document opened by a link has rendered.
  const [pendingFragment, setPendingFragment] = useState<string | null>(null);

  // Load the document list for the selected conversation.
  useEffect(() => {
    if (!conversationId) {
      setDocs([]);
      setListState('idle');
      setSelectedPath(null);
      return;
    }

    let cancelled = false;
    setListState('loading');
    setListError(null);
    setSelectedPath(null);
    setContent(null);
    setContentState('idle');
    setHistory([]);
    setPendingFragment(null);

    getAtxConversationDocs(conversationId)
      .then((response) => {
        if (cancelled) return;
        setDocs(response.docs);
        setConversationStatus(response.status);
        setListState('loaded');
        setSelectedPath(response.docs[0]?.storage_path ?? null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setDocs([]);
        setListState('error');
        setListError(error instanceof Error ? error.message : String(error));
      });

    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // Load the selected document's text through the agent's /file reader.
  useEffect(() => {
    if (!selectedPath) {
      setContent(null);
      setContentState('idle');
      return;
    }

    let cancelled = false;
    setContentState('loading');
    setContentError(null);

    getAtxFileContent(selectedPath)
      .then((text) => {
        if (cancelled) return;
        setContent(text);
        setContentState('loaded');
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setContent(null);
        setContentState('error');
        setContentError(error instanceof Error ? error.message : String(error));
      });

    return () => {
      cancelled = true;
    };
  }, [selectedPath]);

  const selectedDoc = useMemo(
    () => docs.find((doc) => doc.storage_path === selectedPath) ?? null,
    [docs, selectedPath]
  );

  /**
   * Follow a link to another collected document.
   *
   * Selection drives both the rendered content and the side list's highlight, so an
   * in-panel navigation and a click in the list end in the same state.
   */
  const navigateToDoc = useCallback(
    (docPath: string, fragment: string | null) => {
      const target = docs.find((doc) => doc.path === docPath);
      if (!target) return;
      setHistory((previous) => (selectedPath ? [...previous, selectedPath] : previous));
      setPendingFragment(fragment);
      setSelectedPath(target.storage_path);
    },
    [docs, selectedPath]
  );

  const goBack = useCallback(() => {
    setHistory((previous) => {
      if (previous.length === 0) return previous;
      setPendingFragment(null);
      setSelectedPath(previous[previous.length - 1]);
      return previous.slice(0, -1);
    });
  }, []);

  /**
   * Scroll to a linked anchor only once the new document's markup has committed.
   *
   * Selecting the document and scrolling into it cannot happen in the same tick — the
   * heading does not exist yet when the link is clicked.
   */
  useEffect(() => {
    if (contentState !== 'loaded' || !pendingFragment) return;
    const fragment = pendingFragment;
    const frame = requestAnimationFrame(() => {
      document.getElementById(fragment)?.scrollIntoView({ behavior: 'smooth' });
      setPendingFragment(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [contentState, content, pendingFragment]);

  const navigation = useMemo<DocNavigation | undefined>(() => {
    if (!selectedDoc) return undefined;
    return {
      currentPath: selectedDoc.path,
      paths: docs.map((doc) => doc.path),
      onNavigate: navigateToDoc,
    };
  }, [selectedDoc, docs, navigateToDoc]);

  const markdownComponents = useMarkdownComponents({ markdown: content ?? '', navigation });

  if (!conversationId) {
    return <Typography color="text.secondary">Select a conversation to view its documentation.</Typography>;
  }

  if (listState === 'loading') {
    return <CircularProgress size={24} />;
  }

  if (listState === 'error') {
    return (
      <Alert severity="error">
        Could not load documentation for this conversation: {listError}. The agent may be
        unreachable — the documents themselves are unaffected.
      </Alert>
    );
  }

  if (docs.length === 0) {
    if (IN_FLIGHT_STATUSES.has(conversationStatus)) {
      return (
        <Alert severity="info">
          This analysis is still running. Documentation appears here once it completes.
        </Alert>
      );
    }
    if (conversationStatus === 'completed') {
      return (
        <Alert severity="info">
          This analysis completed without producing any documentation artifacts.
        </Alert>
      );
    }
    return (
      <Alert severity="warning">
        This analysis ended as “{conversationStatus}” before producing documentation.
      </Alert>
    );
  }

  return (
    <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
      <Paper variant="outlined" sx={{ width: 260, minWidth: 260, maxHeight: 560, overflow: 'auto' }}>
        <Typography variant="subtitle2" sx={{ px: 1.5, py: 1 }}>
          {docs.length} document{docs.length === 1 ? '' : 's'}
        </Typography>
        <List dense disablePadding>
          {docs.map((doc) => (
            <ListItemButton
              key={doc.storage_path}
              selected={doc.storage_path === selectedPath}
              onClick={() => {
                setPendingFragment(null);
                setSelectedPath(doc.storage_path);
              }}
            >
              <ListItemText
                primary={doc.name}
                secondary={doc.path}
                primaryTypographyProps={{ variant: 'body2' }}
                secondaryTypographyProps={{ variant: 'caption', sx: { wordBreak: 'break-all' } }}
              />
            </ListItemButton>
          ))}
        </List>
      </Paper>

      <Box sx={{ flex: 1, minWidth: 0 }}>
        {/* Following an index link into a subdocument with no route back is its own
            dead end, so the trail is offered whenever one exists. */}
        {history.length > 0 && (
          <Button size="small" startIcon={<ArrowBackIcon />} onClick={goBack} sx={{ mb: 1 }}>
            Back to {docs.find((doc) => doc.storage_path === history[history.length - 1])?.name ??
              'previous document'}
          </Button>
        )}
        {contentState === 'loading' && <CircularProgress size={24} />}
        {contentState === 'error' && (
          <Alert severity="error">
            Could not read {selectedDoc?.name ?? 'this document'}: {contentError}
          </Alert>
        )}
        {contentState === 'loaded' &&
          (content && content.trim() ? (
            <Box sx={{ '& pre': { overflow: 'auto' }, '& table': { display: 'block', overflow: 'auto' } }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {content}
              </ReactMarkdown>
            </Box>
          ) : (
            <Alert severity="info">{selectedDoc?.name ?? 'This document'} is empty.</Alert>
          ))}
      </Box>
    </Box>
  );
}
