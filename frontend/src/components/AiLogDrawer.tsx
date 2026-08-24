import { useRef, useEffect } from 'react';
import { Drawer, Box, Typography, IconButton } from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';
import type { SSEEvent } from '../types';

interface AiLogDrawerProps {
  open: boolean;
  onClose: () => void;
  events: SSEEvent[];
  title?: string;
}

const DRAWER_WIDTH = 400;

export function AiLogDrawer({ open, onClose, events, title = 'AI Logs' }: AiLogDrawerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events]);

  const renderEvent = (event: SSEEvent, index: number) => {
    switch (event.type) {
      case 'content':
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
              {event.text}
            </Typography>
          </Box>
        );
      case 'info':
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'info.main', whiteSpace: 'pre-wrap' }}>
              ℹ {event.text}
            </Typography>
          </Box>
        );
      case 'progress':
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'text.secondary' }}>
              ⏳ [{event.percentage}%] {event.message}
            </Typography>
          </Box>
        );
      case 'error':
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'error.main' }}>
              ✗ {event.message}
            </Typography>
          </Box>
        );
      case 'tool_use':
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'warning.main' }}>
              🔧 {event.tool}
            </Typography>
          </Box>
        );
      case 'tool_result':
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'success.main' }}>
              ✓ {event.tool} completed
            </Typography>
          </Box>
        );
      case 'complete':
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'success.main', fontWeight: 600 }}>
              ✓ Complete — {event.status}
            </Typography>
          </Box>
        );
      default:
        return (
          <Box key={index} sx={{ mb: 0.5 }}>
            <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'text.secondary' }}>
              {JSON.stringify(event)}
            </Typography>
          </Box>
        );
    }
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={{
        '& .MuiDrawer-paper': {
          width: DRAWER_WIDTH,
          boxSizing: 'border-box',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6" sx={{ flex: 1 }}>
          {title}
        </Typography>
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </Box>

      <Box
        ref={scrollRef}
        sx={{
          flex: 1,
          overflow: 'auto',
          p: 2,
          bgcolor: '#fafafa',
        }}
      >
        {events.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
            No events yet...
          </Typography>
        ) : (
          events.map(renderEvent)
        )}
      </Box>
    </Drawer>
  );
}
