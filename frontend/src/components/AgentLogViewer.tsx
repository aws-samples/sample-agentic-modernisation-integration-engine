import { useRef, useEffect } from 'react';
import { Box, Typography } from '@mui/material';
import type { SSEEvent } from '../types';

interface AgentLogViewerProps {
  events: SSEEvent[];
  maxHeight?: number;
  /** Shown when there are no events. Overridden to surface stream failures. */
  emptyMessage?: string;
}

export function AgentLogViewer({
  events,
  maxHeight = 400,
  emptyMessage = 'Waiting for events...',
}: AgentLogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [events]);

  const renderEvent = (event: SSEEvent, index: number) => {
    switch (event.type) {
      case 'content':
        return (
          <Typography key={index} variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
            {event.text}
          </Typography>
        );
      // ATX conversation log — the primary content of the console.
      case 'log':
        return (
          <Typography key={index} variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
            {event.data}
          </Typography>
        );
      // De-noised CLI stdout — secondary, dimmed so it does not compete with
      // the conversation log.
      case 'output':
        return (
          <Typography
            key={index}
            variant="body2"
            sx={{ color: '#8a8aa3', whiteSpace: 'pre-wrap' }}
          >
            {event.data}
          </Typography>
        );
      case 'info':
        return (
          <Typography key={index} variant="body2" sx={{ color: 'info.main', whiteSpace: 'pre-wrap' }}>
            [INFO] {event.text}
          </Typography>
        );
      case 'progress':
        return (
          <Typography key={index} variant="body2" sx={{ color: 'text.secondary' }}>
            [{event.percentage}%] {event.message}
          </Typography>
        );
      case 'error':
        return (
          <Typography key={index} variant="body2" sx={{ color: 'error.main' }}>
            [ERROR] {event.message}
          </Typography>
        );
      case 'tool_use':
        return (
          <Typography key={index} variant="body2" sx={{ color: 'warning.main' }}>
            [TOOL] {event.tool}
          </Typography>
        );
      case 'complete':
        return (
          <Typography key={index} variant="body2" sx={{ color: 'success.main' }}>
            [COMPLETE]{event.status ? ` ${event.status}` : ''}
          </Typography>
        );
      default:
        return (
          <Typography key={index} variant="body2" sx={{ color: 'text.secondary' }}>
            {JSON.stringify(event)}
          </Typography>
        );
    }
  };

  return (
    <Box
      ref={containerRef}
      sx={{
        maxHeight,
        overflow: 'auto',
        bgcolor: '#1a1a2e',
        color: '#e0e0e0',
        borderRadius: 1,
        p: 2,
        fontFamily: 'monospace',
        fontSize: '0.85rem',
      }}
    >
      {events.length === 0 ? (
        <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
          {emptyMessage}
        </Typography>
      ) : (
        events.map(renderEvent)
      )}
    </Box>
  );
}
