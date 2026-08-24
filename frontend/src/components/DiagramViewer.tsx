import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Box,
  ToggleButtonGroup,
  ToggleButton,
  IconButton,
  Tooltip,
  Typography,
  Paper,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import FullscreenIcon from '@mui/icons-material/Fullscreen';
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit';

interface DiagramViewerProps {
  diagrams: Record<string, string>;
}

export function DiagramViewer({ diagrams }: DiagramViewerProps) {
  const [selectedType, setSelectedType] = useState<string>('');
  const [copied, setCopied] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  // svg and the diagram type it belongs to are stored together so the DOM never
  // shows one type's markup while claiming to display another.
  const [rendered, setRendered] = useState<{ type: string; svg: string }>({
    type: '',
    svg: '',
  });
  const containerRef = useRef<HTMLDivElement>(null);
  const diagramRef = useRef<HTMLDivElement>(null);

  const diagramTypes = Object.keys(diagrams ?? {});

  // Set initial selected type
  useEffect(() => {
    if (diagramTypes.length > 0 && !selectedType) {
      setSelectedType(diagramTypes[0]);
    }
  }, [diagramTypes, selectedType]);

  // Render mermaid diagram
  useEffect(() => {
    if (!selectedType || !diagrams[selectedType]) {
      setRendered({ type: selectedType, svg: '' });
      return;
    }

    let cancelled = false;
    const type = selectedType;

    // Clear immediately so the previous type's svg is never shown as if it were
    // this one while the async render is in flight.
    setRendered({ type: '', svg: '' });

    async function renderDiagram() {
      try {
        const mermaid = await import('mermaid');
        mermaid.default.initialize({
          startOnLoad: false,
          theme: 'default',
          // 'strict' (Mermaid's default) HTML-escapes diagram labels and blocks
          // script/click handlers. The diagram source is AI-generated, so it is
          // untrusted input rendered via dangerouslySetInnerHTML — 'loose' would
          // allow injected markup/script to reach the DOM (CWE-79). This app
          // renders only standard class/sequence/flow diagrams, which need no
          // raw HTML, so 'strict' does not lose functionality.
          securityLevel: 'strict',
        });
        const id = `mermaid-${type}-${Date.now()}`;
        const { svg } = await mermaid.default.render(id, diagrams[type]);
        if (!cancelled) {
          setRendered({ type, svg });
        }
      } catch (err) {
        if (!cancelled) {
          setRendered({
            type,
            svg: `<pre style="color: #d32f2f; padding: 16px;">Failed to render diagram: ${err instanceof Error ? err.message : 'Unknown error'}</pre>`,
          });
        }
      }
    }

    renderDiagram();
    return () => {
      cancelled = true;
    };
  }, [selectedType, diagrams]);

  const handleTypeChange = (_event: React.MouseEvent<HTMLElement>, newType: string | null) => {
    if (newType) {
      setSelectedType(newType);
    }
  };

  const handleCopy = useCallback(async () => {
    if (selectedType && diagrams[selectedType]) {
      try {
        await navigator.clipboard.writeText(diagrams[selectedType]);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // Clipboard API not available
      }
    }
  }, [selectedType, diagrams]);

  const handleFullscreen = useCallback(() => {
    if (!containerRef.current) return;

    if (!isFullscreen) {
      if (containerRef.current.requestFullscreen) {
        containerRef.current.requestFullscreen();
        setIsFullscreen(true);
      }
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen();
        setIsFullscreen(false);
      }
    }
  }, [isFullscreen]);

  // Listen for fullscreen change events
  useEffect(() => {
    const handleFsChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFsChange);
    return () => document.removeEventListener('fullscreenchange', handleFsChange);
  }, []);

  if (!diagrams || diagramTypes.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ p: 2 }}>
        No diagrams available.
      </Typography>
    );
  }

  return (
    <Paper
      ref={containerRef}
      elevation={0}
      sx={{
        border: '1px solid #e0e0e0',
        borderRadius: 1,
        bgcolor: isFullscreen ? '#fff' : 'transparent',
        height: isFullscreen ? '100vh' : 'auto',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 1,
          borderBottom: '1px solid #e0e0e0',
        }}
      >
        <ToggleButtonGroup
          value={selectedType}
          exclusive
          onChange={handleTypeChange}
          size="small"
        >
          {diagramTypes.map((type) => (
            <ToggleButton key={type} value={type}>
              {type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Box>
          <Tooltip title={copied ? 'Copied!' : 'Copy Mermaid code'}>
            <IconButton size="small" onClick={handleCopy}>
              <ContentCopyIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}>
            <IconButton size="small" onClick={handleFullscreen}>
              {isFullscreen ? (
                <FullscreenExitIcon fontSize="small" />
              ) : (
                <FullscreenIcon fontSize="small" />
              )}
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      <Box
        ref={diagramRef}
        data-testid="diagram-render-area"
        data-diagram-type={selectedType}
        data-rendered-type={rendered.type}
        sx={{
          p: 2,
          flexGrow: 1,
          overflow: 'auto',
          minHeight: 300,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          '& svg': {
            maxWidth: '100%',
            height: 'auto',
          },
        }}
        dangerouslySetInnerHTML={{ __html: rendered.svg }}
      />
    </Paper>
  );
}
