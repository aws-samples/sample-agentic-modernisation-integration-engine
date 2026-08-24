import { useCallback, useMemo, useRef, useState } from 'react';
import { Box, Typography, IconButton, Collapse, List, ListItemButton, ListItemText } from '@mui/material';
import { UnfoldLess, UnfoldMore } from '@mui/icons-material';

export type DiffCategory = 'source' | 'documentation';

interface DiffLine {
  type: 'added' | 'removed' | 'unchanged';
  content: string;
  oldLineNumber?: number;
  newLineNumber?: number;
}

export interface FileDiff {
  filename: string;
  lines: DiffLine[];
  status?: string;
  /** Generated documentation vs a change to the repository's own files. */
  category?: DiffCategory;
  /** The agent capped this file's lines; the full content is in the download. */
  truncated?: boolean;
}

interface EnhancedFileComparisonProps {
  files: FileDiff[];
}

/**
 * Group order and labels.
 *
 * Source first: it answers the question a reader of a transformation result asks
 * first. Documentation second, because generated markdown is output *about* the
 * codebase rather than a change to it — an analysis-type run produces dozens of these
 * and, ungrouped, they bury (or appear to replace) the source diff.
 */
const GROUP_ORDER: { key: DiffCategory; label: string }[] = [
  { key: 'source', label: 'Source changes' },
  { key: 'documentation', label: 'Generated documentation' },
];

function DiffLineRow({ line }: { line: DiffLine }) {
  const bgColor =
    line.type === 'added'
      ? 'rgba(46, 160, 67, 0.15)'
      : line.type === 'removed'
        ? 'rgba(248, 81, 73, 0.15)'
        : 'transparent';

  const textColor =
    line.type === 'added'
      ? '#2ea043'
      : line.type === 'removed'
        ? '#f85149'
        : 'text.primary';

  const prefix = line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' ';

  return (
    <Box
      sx={{
        display: 'flex',
        // A wrapped line makes the row taller than one line, so the gutters have to be
        // told where to sit. `flex-start` puts them against the first visual line of the
        // content, as GitHub's diff view does — the default `stretch` would spread the
        // 20px line boxes over the whole row and `center` would float them into the
        // middle of a wrapped block. The background stays on this row, so it covers
        // every wrapped line rather than only the first.
        alignItems: 'flex-start',
        bgcolor: bgColor,
        '&:hover': { bgcolor: line.type === 'unchanged' ? 'action.hover' : bgColor },
      }}
    >
      <Box
        sx={{
          width: 50,
          minWidth: 50,
          textAlign: 'right',
          pr: 1,
          color: 'text.secondary',
          fontSize: '0.75rem',
          fontFamily: 'monospace',
          borderRight: '1px solid',
          borderColor: 'divider',
          userSelect: 'none',
          lineHeight: '20px',
        }}
      >
        {line.oldLineNumber ?? ''}
      </Box>
      <Box
        sx={{
          width: 50,
          minWidth: 50,
          textAlign: 'right',
          pr: 1,
          color: 'text.secondary',
          fontSize: '0.75rem',
          fontFamily: 'monospace',
          borderRight: '1px solid',
          borderColor: 'divider',
          userSelect: 'none',
          lineHeight: '20px',
        }}
      >
        {line.newLineNumber ?? ''}
      </Box>
      <Box
        sx={{
          flex: 1,
          pl: 1,
          fontFamily: 'monospace',
          fontSize: '0.8rem',
          color: textColor,
          // Wrap rather than clip. `pre` + `text-overflow: ellipsis` silently dropped
          // the tail of any over-wide line and there was no way to reach it — not by
          // scrolling, not by selecting.
          //
          // `pre-wrap` keeps leading whitespace, which in a code diff is meaning, while
          // allowing the line to break. `anywhere` is what makes a single unbroken token
          // (minified JS, a base64 blob, a long URL) break at all: those contain no
          // break opportunity, so a rule that only breaks at spaces still overflows.
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          lineHeight: '20px',
        }}
      >
        {prefix} {line.content}
      </Box>
    </Box>
  );
}

function CollapsibleSection({ lines, startIndex }: { lines: DiffLine[]; startIndex: number }) {
  const [expanded, setExpanded] = useState(false);

  if (lines.length <= 6) {
    return (
      <>
        {lines.map((line, i) => (
          <DiffLineRow key={startIndex + i} line={line} />
        ))}
      </>
    );
  }

  return (
    <>
      {expanded ? (
        <>
          <Box sx={{ display: 'flex', alignItems: 'center', bgcolor: 'action.hover', px: 1, py: 0.25 }}>
            <IconButton size="small" onClick={() => setExpanded(false)}>
              <UnfoldLess fontSize="small" />
            </IconButton>
            <Typography variant="caption" color="text.secondary">
              Collapse {lines.length} unchanged lines
            </Typography>
          </Box>
          {lines.map((line, i) => (
            <DiffLineRow key={startIndex + i} line={line} />
          ))}
        </>
      ) : (
        <Box sx={{ display: 'flex', alignItems: 'center', bgcolor: 'action.hover', px: 1, py: 0.25 }}>
          <IconButton size="small" onClick={() => setExpanded(true)}>
            <UnfoldMore fontSize="small" />
          </IconButton>
          <Typography variant="caption" color="text.secondary">
            Show {lines.length} unchanged lines
          </Typography>
        </Box>
      )}
    </>
  );
}

function groupLines(lines: DiffLine[]): { type: 'changed' | 'unchanged'; lines: DiffLine[]; startIndex: number }[] {
  const groups: { type: 'changed' | 'unchanged'; lines: DiffLine[]; startIndex: number }[] = [];
  let currentGroup: DiffLine[] = [];
  let currentType: 'changed' | 'unchanged' = 'unchanged';
  let startIndex = 0;

  for (let i = 0; i < lines.length; i++) {
    const lineType = lines[i].type === 'unchanged' ? 'unchanged' : 'changed';
    if (lineType !== currentType && currentGroup.length > 0) {
      groups.push({ type: currentType, lines: currentGroup, startIndex });
      currentGroup = [];
      startIndex = i;
      currentType = lineType;
    }
    currentGroup.push(lines[i]);
  }

  if (currentGroup.length > 0) {
    groups.push({ type: currentType, lines: currentGroup, startIndex });
  }

  return groups;
}

const STATUS_COLOR: Record<string, string> = {
  added: '#2ea043',
  modified: '#d29922',
  deleted: '#f85149',
};

/** Accessible name for one file option — path, status, and whether it was capped. */
function optionLabel(file: FileDiff): string {
  return [file.filename, file.status, file.truncated ? 'content truncated' : null]
    .filter(Boolean)
    .join(', ');
}

/**
 * Paired file navigation and diff content.
 *
 * The file list sits on the right and the diff on the left, matching the ATX analysis
 * Documentation tab. It replaced a horizontal `<Tabs>` strip: with a real run
 * producing dozens of files that strip scrolled sideways and truncated the filenames,
 * which are the only thing distinguishing one entry from another. Here the full
 * relative path wraps rather than being clipped.
 *
 * Files are grouped by `category` so a run's composition is legible — an
 * analysis-type transformation generates documentation and edits no source, and that
 * must read as a result rather than as missing data. Both groups are always listed
 * and always viewable.
 *
 * Below the `md` breakpoint the two panes stack (list above diff) so the diff is
 * never squeezed to a sliver.
 */
export function EnhancedFileComparison({ files }: EnhancedFileComparisonProps) {
  const [selectedFilename, setSelectedFilename] = useState<string | null>(null);
  const optionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  // Source first, documentation second; a file with no category is treated as
  // source, which is the safe default (it is a repository file until shown otherwise).
  const groups = useMemo(
    () =>
      GROUP_ORDER.map(({ key, label }) => ({
        key,
        label,
        files: files.filter((file) => (file.category ?? 'source') === key),
      })).filter((group) => group.files.length > 0),
    [files]
  );

  const orderedFiles = useMemo(() => groups.flatMap((group) => group.files), [groups]);

  // Selection falls back to the first file so the diff pane is never blank while
  // files exist — and re-resolves if the payload changes underneath it.
  const currentFile =
    orderedFiles.find((file) => file.filename === selectedFilename) ?? orderedFiles[0] ?? null;

  const focusOption = useCallback((filename: string) => {
    setSelectedFilename(filename);
    optionRefs.current[filename]?.focus();
  }, []);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (orderedFiles.length === 0) return;
      const currentIndex = Math.max(
        0,
        orderedFiles.findIndex((file) => file.filename === currentFile?.filename)
      );

      let nextIndex: number | null = null;
      if (event.key === 'ArrowDown') nextIndex = Math.min(orderedFiles.length - 1, currentIndex + 1);
      else if (event.key === 'ArrowUp') nextIndex = Math.max(0, currentIndex - 1);
      else if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = orderedFiles.length - 1;

      if (nextIndex !== null) {
        event.preventDefault();
        focusOption(orderedFiles[nextIndex].filename);
      }
    },
    [orderedFiles, currentFile, focusOption]
  );

  if (files.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography color="text.secondary">No file changes to display</Typography>
      </Box>
    );
  }

  const lineGroups = groupLines(currentFile?.lines ?? []);

  return (
    <Box
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 1,
        overflow: 'hidden',
        display: 'flex',
        // `row-reverse` keeps the list first in the DOM (so it is first in tab and
        // screen-reader order, being the navigation) while painting it on the right.
        flexDirection: { xs: 'column', md: 'row-reverse' },
        alignItems: 'stretch',
      }}
    >
      <Box
        component="nav"
        aria-label="Changed files"
        sx={{
          width: { xs: '100%', md: 300 },
          minWidth: { md: 300 },
          flexShrink: 0,
          maxHeight: { xs: 240, md: 600 },
          overflow: 'auto',
          bgcolor: 'background.paper',
          borderBottom: { xs: '1px solid', md: 'none' },
          borderLeft: { md: '1px solid' },
          borderColor: 'divider',
        }}
      >
        <List
          dense
          disablePadding
          role="listbox"
          aria-label="Changed files"
          aria-orientation="vertical"
          onKeyDown={handleKeyDown}
        >
          {groups.map((group) => (
            <Box key={group.key} role="group" aria-label={`${group.label} (${group.files.length})`}>
              <Box
                sx={{
                  px: 1.5,
                  py: 0.75,
                  bgcolor: 'action.hover',
                  borderBottom: '1px solid',
                  borderColor: 'divider',
                  position: 'sticky',
                  top: 0,
                  zIndex: 1,
                }}
              >
                <Typography variant="caption" sx={{ fontWeight: 600 }}>
                  {group.label} ({group.files.length})
                </Typography>
              </Box>
              {group.files.map((file) => {
                const selected = file.filename === currentFile?.filename;
                return (
                  <ListItemButton
                    key={file.filename}
                    ref={(node: HTMLDivElement | null) => {
                      optionRefs.current[file.filename] = node;
                    }}
                    role="option"
                    aria-selected={selected}
                    aria-label={optionLabel(file)}
                    selected={selected}
                    // Roving tabindex: one stop for the whole list, arrows move within.
                    tabIndex={selected ? 0 : -1}
                    onClick={() => setSelectedFilename(file.filename)}
                    title={file.filename}
                    sx={{ alignItems: 'flex-start', py: 0.75 }}
                  >
                    <ListItemText
                      primary={file.filename}
                      secondary={
                        <>
                          {file.status && (
                            <Typography
                              component="span"
                              variant="caption"
                              sx={{ color: STATUS_COLOR[file.status] ?? 'text.secondary' }}
                            >
                              {file.status}
                            </Typography>
                          )}
                          {file.status && file.truncated && (
                            <Typography component="span" variant="caption" color="text.secondary" aria-hidden>
                              {' · '}
                            </Typography>
                          )}
                          {file.truncated && (
                            <Typography component="span" variant="caption" color="text.secondary">
                              truncated
                            </Typography>
                          )}
                        </>
                      }
                      primaryTypographyProps={{
                        variant: 'body2',
                        // The full relative path is the label; clipping it to
                        // "…upgrade.md" removes the only distinguishing detail.
                        sx: { wordBreak: 'break-all', fontFamily: 'monospace', fontSize: '0.75rem' },
                      }}
                      secondaryTypographyProps={{ variant: 'caption' }}
                    />
                  </ListItemButton>
                );
              })}
            </Box>
          ))}
        </List>
      </Box>

      <Box
        sx={{ flex: 1, minWidth: 0, maxHeight: 600, overflow: 'auto' }}
        role="region"
        aria-label={currentFile ? `Diff for ${currentFile.filename}` : 'Diff'}
      >
        {currentFile?.truncated && currentFile.lines.length === 0 && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', p: 1.5 }}>
            This file's content was omitted to bound the response. Download the transformed
            tree to see it in full.
          </Typography>
        )}
        {lineGroups.map((group, idx) =>
          group.type === 'unchanged' ? (
            <CollapsibleSection key={idx} lines={group.lines} startIndex={group.startIndex} />
          ) : (
            <Collapse key={idx} in>
              {group.lines.map((line, i) => (
                <DiffLineRow key={group.startIndex + i} line={line} />
              ))}
            </Collapse>
          )
        )}
      </Box>
    </Box>
  );
}
