import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EnhancedFileComparison, type FileDiff } from './EnhancedFileComparison';

/**
 * File navigation for the transform results diff.
 *
 * The reported defect: a horizontal `<Tabs>` strip above the diff. With a real run
 * producing dozens of files it scrolled sideways and truncated the filenames — the only
 * thing distinguishing one entry from another. It is now a vertical listbox beside the
 * diff, grouped into generated documentation and source edits so a run's composition is
 * legible.
 *
 * Pinned here: full paths are the labels, the groups carry their counts, selection
 * switches the diff pane, and the list is a real listbox rather than styled divs.
 */

const SOURCE_FILE: FileDiff = {
  filename: 'backend/src/main/java/com/taskmanager/infrastructure/web/TaskController.java',
  status: 'modified',
  category: 'source',
  lines: [
    { type: 'unchanged', content: 'package com.taskmanager;', oldLineNumber: 1, newLineNumber: 1 },
    { type: 'removed', content: 'int legacy = 1;', oldLineNumber: 2 },
    { type: 'added', content: 'int modern = 2;', newLineNumber: 2 },
  ],
};

const DOC_FILE: FileDiff = {
  filename: 'ATXDocumentation/analysis/java-version-upgrade.md',
  status: 'added',
  category: 'documentation',
  lines: [{ type: 'added', content: '# Java version upgrade', newLineNumber: 1 }],
};

const SECOND_DOC_FILE: FileDiff = {
  filename: 'ATXDocumentation/architecture/components.md',
  status: 'added',
  category: 'documentation',
  lines: [{ type: 'added', content: '# Components', newLineNumber: 1 }],
};

describe('EnhancedFileComparison file list', () => {
  it('labels each entry with its full relative path, not a truncated tail', () => {
    render(<EnhancedFileComparison files={[SOURCE_FILE, DOC_FILE]} />);

    // The whole path is the rendered label and is also exposed on hover.
    const option = screen.getByRole('option', { name: new RegExp(SOURCE_FILE.filename) });
    expect(option).toHaveAttribute('title', SOURCE_FILE.filename);
    expect(screen.getByText(SOURCE_FILE.filename)).toBeInTheDocument();
    expect(screen.getByText(DOC_FILE.filename)).toBeInTheDocument();
  });

  it('separates the two groups and shows their counts', () => {
    render(<EnhancedFileComparison files={[SOURCE_FILE, DOC_FILE, SECOND_DOC_FILE]} />);

    expect(screen.getByText('Source changes (1)')).toBeInTheDocument();
    expect(screen.getByText('Generated documentation (2)')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Source changes (1)' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Generated documentation (2)' })).toBeInTheDocument();
  });

  it('omits a group that has no files rather than showing an empty heading', () => {
    render(<EnhancedFileComparison files={[DOC_FILE, SECOND_DOC_FILE]} />);

    expect(screen.getByText('Generated documentation (2)')).toBeInTheDocument();
    expect(screen.queryByText(/^Source changes/)).not.toBeInTheDocument();
  });

  it('switches the diff pane to the selected file', () => {
    render(<EnhancedFileComparison files={[SOURCE_FILE, DOC_FILE]} />);

    // First file is selected by default, so the pane is never blank.
    expect(screen.getByText(/int modern = 2;/)).toBeInTheDocument();
    expect(screen.queryByText(/# Java version upgrade/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: new RegExp(DOC_FILE.filename) }));

    expect(screen.getByText(/# Java version upgrade/)).toBeInTheDocument();
    expect(screen.queryByText(/int modern = 2;/)).not.toBeInTheDocument();
  });

  it('marks the selected entry so the state is obvious to assistive tech', () => {
    render(<EnhancedFileComparison files={[SOURCE_FILE, DOC_FILE]} />);

    const source = screen.getByRole('option', { name: new RegExp(SOURCE_FILE.filename) });
    const doc = screen.getByRole('option', { name: new RegExp(DOC_FILE.filename) });

    expect(source).toHaveAttribute('aria-selected', 'true');
    expect(doc).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(doc);

    expect(doc).toHaveAttribute('aria-selected', 'true');
    expect(source).toHaveAttribute('aria-selected', 'false');
  });

  it('is a labelled listbox with roving focus, navigable by arrow keys', () => {
    render(<EnhancedFileComparison files={[SOURCE_FILE, DOC_FILE, SECOND_DOC_FILE]} />);

    const listbox = screen.getByRole('listbox', { name: 'Changed files' });
    expect(listbox).toBeInTheDocument();

    const options = screen.getAllByRole('option');
    // One tab stop for the list; arrows move within it.
    expect(options[0]).toHaveAttribute('tabindex', '0');
    expect(options[1]).toHaveAttribute('tabindex', '-1');

    fireEvent.keyDown(listbox, { key: 'ArrowDown' });
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/# Java version upgrade/)).toBeInTheDocument();

    fireEvent.keyDown(listbox, { key: 'End' });
    expect(screen.getAllByRole('option')[2]).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(listbox, { key: 'Home' });
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true');
  });

  it("reports each entry's status and whether its content was capped", () => {
    const truncated: FileDiff = { ...DOC_FILE, truncated: true };
    render(<EnhancedFileComparison files={[SOURCE_FILE, truncated]} />);

    expect(screen.getByText('modified')).toBeInTheDocument();
    expect(screen.getByText('added')).toBeInTheDocument();
    expect(screen.getByText('truncated')).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: /content truncated/ })
    ).toBeInTheDocument();
  });

  it('treats a file with no category as a source change', () => {
    render(<EnhancedFileComparison files={[{ filename: 'pom.xml', lines: [] }]} />);

    expect(screen.getByText('Source changes (1)')).toBeInTheDocument();
  });

  it('keeps the collapse-unchanged behaviour for long unchanged runs', () => {
    const withLongContext: FileDiff = {
      filename: 'App.java',
      status: 'modified',
      category: 'source',
      lines: [
        ...Array.from({ length: 8 }, (_, i) => ({
          type: 'unchanged' as const,
          content: `context ${i}`,
          oldLineNumber: i + 1,
          newLineNumber: i + 1,
        })),
        { type: 'added' as const, content: 'new line', newLineNumber: 9 },
      ],
    };

    render(<EnhancedFileComparison files={[withLongContext]} />);

    expect(screen.getByText('Show 8 unchanged lines')).toBeInTheDocument();
    expect(screen.queryByText('context 0')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '' }));

    expect(screen.getByText('context 0')).toBeInTheDocument();
    expect(screen.getByText('Collapse 8 unchanged lines')).toBeInTheDocument();
  });

  it('shows the empty state when there is nothing to compare', () => {
    render(<EnhancedFileComparison files={[]} />);

    expect(screen.getByText('No file changes to display')).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});

/**
 * Long diff lines.
 *
 * The reported defect: the content cell rendered with `white-space: pre` plus
 * `overflow: hidden` / `text-overflow: ellipsis`, so anything wider than the pane was
 * clipped to an ellipsis. The rest of the line was unreachable — not scrollable to and
 * not selectable. It must wrap to fit the pane instead.
 *
 * Pinned here: the whole line is in the DOM, indentation survives (this is code), an
 * unbroken 10k-character token still wraps, and the line numbers stay aligned to the
 * first visual line of a wrapped row rather than being centred or stretched.
 */

/** A minified-JS / base64 / long-URL stand-in: no break opportunity anywhere in it. */
const UNBROKEN_TOKEN = 'a1B2c3D4'.repeat(1250); // 10,000 chars, zero spaces

const INDENTED_CONTENT = '        return service.doSomethingWithAVeryLongName(argumentOne, argumentTwo);';

/**
 * The content cell for a rendered diff line.
 *
 * Found by its exact text rather than a test id so the assertion is on what a reader
 * would actually be able to read: the prefix, one space, and the line verbatim. An
 * implementation that shortened the string would not match.
 */
function contentCellFor(container: HTMLElement, prefix: string, content: string): HTMLElement {
  const expected = `${prefix} ${content}`;
  const cell = Array.from(container.querySelectorAll<HTMLElement>('div')).find(
    (el) => el.children.length === 0 && el.textContent === expected
  );
  if (!cell) throw new Error(`No diff content cell rendered the full line: ${content.slice(0, 40)}…`);
  return cell;
}

describe('EnhancedFileComparison long lines', () => {
  it('renders an over-wide line in full rather than clipping it to an ellipsis', () => {
    const file: FileDiff = {
      filename: 'dist/bundle.min.js',
      status: 'modified',
      category: 'source',
      lines: [{ type: 'added', content: UNBROKEN_TOKEN, newLineNumber: 1 }],
    };

    const { container } = render(<EnhancedFileComparison files={[file]} />);

    const cell = contentCellFor(container, '+', UNBROKEN_TOKEN);
    // The whole 10,000 characters, not a prefix of them.
    expect(cell.textContent).toBe(`+ ${UNBROKEN_TOKEN}`);
    expect(cell.textContent).toHaveLength(UNBROKEN_TOKEN.length + 2);
    expect(cell.textContent).not.toMatch(/…|\.\.\./);
  });

  it('wraps long content instead of hiding the overflow', () => {
    const file: FileDiff = {
      filename: 'dist/bundle.min.js',
      status: 'modified',
      category: 'source',
      lines: [{ type: 'added', content: UNBROKEN_TOKEN, newLineNumber: 1 }],
    };

    const { container } = render(<EnhancedFileComparison files={[file]} />);
    const style = getComputedStyle(contentCellFor(container, '+', UNBROKEN_TOKEN));

    // Wrapping, but still whitespace-preserving: this is code.
    expect(style.whiteSpace).toBe('pre-wrap');
    // Nothing is clipped away any more.
    expect(style.textOverflow).not.toBe('ellipsis');
    expect(style.overflow).not.toBe('hidden');
    // A token with no spaces in it has to break mid-token or it overflows the pane.
    expect([style.overflowWrap, style.wordBreak]).toContain('anywhere');
  });

  it('preserves leading indentation when a line wraps', () => {
    const file: FileDiff = {
      filename: 'App.java',
      status: 'modified',
      category: 'source',
      lines: [{ type: 'unchanged', content: INDENTED_CONTENT, oldLineNumber: 4, newLineNumber: 4 }],
    };

    const { container } = render(<EnhancedFileComparison files={[file]} />);
    const cell = contentCellFor(container, ' ', INDENTED_CONTENT);

    // The eight leading spaces are still there, verbatim, after the prefix and its space.
    expect(cell.textContent).toBe(`  ${INDENTED_CONTENT}`);
    expect(getComputedStyle(cell).whiteSpace).toBe('pre-wrap');
  });

  it('aligns the line numbers to the first visual line of a wrapped row', () => {
    const file: FileDiff = {
      filename: 'dist/bundle.min.js',
      status: 'modified',
      category: 'source',
      lines: [{ type: 'added', content: UNBROKEN_TOKEN, newLineNumber: 1 }],
    };

    const { container } = render(<EnhancedFileComparison files={[file]} />);
    const row = contentCellFor(container, '+', UNBROKEN_TOKEN).parentElement as HTMLElement;

    // Once the row is taller than one line the gutters must sit at its top, as in
    // GitHub's diff view — not centred against the wrapped block, not stretched.
    expect(getComputedStyle(row).alignItems).toBe('flex-start');
    // The row still carries the per-type background, so it covers every wrapped line.
    expect(getComputedStyle(row).backgroundColor).toBe('rgba(46, 160, 67, 0.15)');
  });
});
