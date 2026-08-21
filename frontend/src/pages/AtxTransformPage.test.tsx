import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

/**
 * The results page renders the agent's diff payload through `EnhancedFileComparison`,
 * which lists files by `filename` and rows by `lines[]`.
 *
 * Two defects are pinned here:
 *
 * - the agent used to return `{path, status, before, after, diff}`, so `file.filename`
 *   was undefined and `file.lines` was never an array; the page's `?? 'unknown'` /
 *   `?? []` fallbacks turned that into a list of "unknown" files with zero rows,
 *   silently;
 * - a `comprehensive-codebase-analysis` run showed 32 markdown files and no source
 *   diff, with nothing to distinguish "generated docs, changed no code" from "the
 *   source changes are missing". The header now states both counts.
 */

const getDiff = vi.fn();
const getDiffSummary = vi.fn();
const downloadTransformedTree = vi.fn();

vi.mock('../services/api', () => ({
  getDiff: (...args: unknown[]) => getDiff(...args),
  getDiffSummary: (...args: unknown[]) => getDiffSummary(...args),
  downloadTransformedTree: (...args: unknown[]) => downloadTransformedTree(...args),
}));

const { AtxTransformPage } = await import('./AtxTransformPage');

const REPO_ID = 'abc123def456';

/** Exactly the shape `GET /diff/{repo_id}` now returns. */
const AGENT_DIFF = {
  repo_id: REPO_ID,
  truncated: false,
  omitted_files: 0,
  files: [
    {
      filename: 'pom.xml',
      status: 'modified',
      category: 'source',
      lines: [
        { type: 'unchanged', content: '<project>', old_line_number: 1, new_line_number: 1 },
        { type: 'removed', content: '  <source>8</source>', old_line_number: 2, new_line_number: null },
        { type: 'added', content: '  <source>17</source>', old_line_number: null, new_line_number: 2 },
        { type: 'unchanged', content: '</project>', old_line_number: 3, new_line_number: 3 },
      ],
    },
    {
      filename: 'src/main/java/App.java',
      status: 'added',
      category: 'source',
      lines: [{ type: 'added', content: 'class App {}', old_line_number: null, new_line_number: 1 }],
    },
  ],
};

const AGENT_SUMMARY = {
  repo_id: REPO_ID,
  total_files: 42,
  changed_files: 2,
  added: 1,
  modified: 1,
  deleted: 0,
  unchanged: 40,
  additions: 2,
  deletions: 1,
  source_files_changed: 2,
  documentation_files_changed: 0,
  changed_by_category: {
    source: { files: 2, additions: 2, deletions: 1 },
    documentation: { files: 0, additions: 0, deletions: 0 },
  },
  has_changes: true,
};

/** The user's case: an analysis-type run that generates docs and edits no source. */
const DOCS_ONLY_DIFF = {
  repo_id: REPO_ID,
  truncated: false,
  omitted_files: 0,
  files: [
    {
      filename: 'ATXDocumentation/README.md',
      status: 'added',
      category: 'documentation',
      lines: [{ type: 'added', content: '# Analysis', old_line_number: null, new_line_number: 1 }],
    },
    {
      filename: 'ATXDocumentation/analysis/tech-debt.md',
      status: 'added',
      category: 'documentation',
      lines: [{ type: 'added', content: '# Tech debt', old_line_number: null, new_line_number: 1 }],
    },
  ],
};

const DOCS_ONLY_SUMMARY = {
  ...AGENT_SUMMARY,
  changed_files: 2,
  added: 2,
  modified: 0,
  additions: 2,
  deletions: 0,
  source_files_changed: 0,
  documentation_files_changed: 2,
  changed_by_category: {
    source: { files: 0, additions: 0, deletions: 0 },
    documentation: { files: 2, additions: 2, deletions: 0 },
  },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/transform-results/${REPO_ID}`]}>
      <Routes>
        <Route path="/transform-results/:id" element={<AtxTransformPage />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  getDiff.mockReset();
  getDiffSummary.mockReset();
  downloadTransformedTree.mockReset();
  getDiff.mockResolvedValue(AGENT_DIFF);
  getDiffSummary.mockResolvedValue(AGENT_SUMMARY);
  downloadTransformedTree.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AtxTransformPage diff rendering', () => {
  it('renders real full paths in the file list, not "unknown"', async () => {
    renderPage();

    expect(await screen.findByRole('option', { name: /^pom\.xml/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /^src\/main\/java\/App\.java/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /unknown/ })).not.toBeInTheDocument();
  });

  it('renders the added and removed lines of a modified file', async () => {
    renderPage();

    await screen.findByRole('option', { name: /^pom\.xml/ });

    expect(screen.getByText(/<source>8<\/source>/)).toBeInTheDocument();
    expect(screen.getByText(/<source>17<\/source>/)).toBeInTheDocument();
    expect(screen.queryByText('No file changes to display')).not.toBeInTheDocument();
  });

  it('reports changed-file and line counts from the summary', async () => {
    renderPage();

    expect(
      await screen.findByText('2 files changed, 2 additions, 1 deletions')
    ).toBeInTheDocument();
  });

  it('states both the source and documentation counts for a mixed run', async () => {
    renderPage();

    expect(
      await screen.findByText('2 source files changed, 0 documentation files generated')
    ).toBeInTheDocument();
  });

  it('shows the empty state when the agent reports no changed files', async () => {
    getDiff.mockResolvedValue({ repo_id: REPO_ID, files: [], truncated: false, omitted_files: 0 });
    getDiffSummary.mockResolvedValue({
      ...AGENT_SUMMARY,
      changed_files: 0,
      has_changes: false,
      source_files_changed: 0,
      documentation_files_changed: 0,
    });

    renderPage();

    expect(await screen.findByText('No file changes to display')).toBeInTheDocument();
  });
});

describe('AtxTransformPage run composition', () => {
  it('reports zero source changes for a documentation-only run without implying loss', async () => {
    getDiff.mockResolvedValue(DOCS_ONLY_DIFF);
    getDiffSummary.mockResolvedValue(DOCS_ONLY_SUMMARY);

    renderPage();

    expect(
      await screen.findByText('0 source files changed, 2 documentation files generated')
    ).toBeInTheDocument();
    expect(
      screen.getByText(/generated documentation and made no source code changes/i)
    ).toBeInTheDocument();

    // And the documentation is still there to read — grouped, not filtered out.
    expect(screen.getByRole('option', { name: /ATXDocumentation\/README\.md/ })).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: /ATXDocumentation\/analysis\/tech-debt\.md/ })
    ).toBeInTheDocument();
    expect(screen.getByText('Generated documentation (2)')).toBeInTheDocument();
    expect(screen.queryByText('No file changes to display')).not.toBeInTheDocument();
  });

  it('does not claim a documentation-only run when source files changed too', async () => {
    getDiff.mockResolvedValue({
      ...AGENT_DIFF,
      files: [...AGENT_DIFF.files, ...DOCS_ONLY_DIFF.files],
    });
    getDiffSummary.mockResolvedValue({
      ...AGENT_SUMMARY,
      changed_files: 4,
      source_files_changed: 2,
      documentation_files_changed: 2,
    });

    renderPage();

    expect(
      await screen.findByText('2 source files changed, 2 documentation files generated')
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/made no source code changes/i)
    ).not.toBeInTheDocument();
    expect(screen.getByText('Source changes (2)')).toBeInTheDocument();
    expect(screen.getByText('Generated documentation (2)')).toBeInTheDocument();
  });

  it('classifies files itself when the agent payload carries no category', async () => {
    getDiff.mockResolvedValue({
      repo_id: REPO_ID,
      truncated: false,
      omitted_files: 0,
      files: [
        { filename: 'ATXDocumentation/README.md', status: 'added', lines: [] },
        { filename: 'pom.xml', status: 'modified', lines: [] },
      ],
    });
    getDiffSummary.mockResolvedValue({
      total_files: 10,
      changed_files: 2,
      additions: 1,
      deletions: 1,
    });

    renderPage();

    expect(
      await screen.findByText('1 source file changed, 1 documentation file generated')
    ).toBeInTheDocument();
  });
});

describe('AtxTransformPage download', () => {
  it('downloads the whole transformed tree for the routed transformation', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /download code/i }));

    await waitFor(() => expect(downloadTransformedTree).toHaveBeenCalledWith(REPO_ID));
  });

  it('offers the download even when there is nothing to show in the diff', async () => {
    getDiff.mockResolvedValue({ repo_id: REPO_ID, files: [], truncated: false, omitted_files: 0 });

    renderPage();

    const button = await screen.findByRole('button', { name: /download code/i });
    expect(button).toBeEnabled();
  });

  it("surfaces the agent's reason when the download is refused", async () => {
    downloadTransformedTree.mockRejectedValue(
      new Error('Transformed tree is 812.0 MB uncompressed, which exceeds the 500 MB download limit.')
    );

    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /download code/i }));

    expect(await screen.findByText(/exceeds the 500 MB download limit/)).toBeInTheDocument();
  });
});

/**
 * Create PR was removed from this page. The transform agent keeps
 * `POST /create-file-pr/{repo_id}` and `GET /pr-preview/{repo_id}` — this is the
 * removal of a UI affordance, not of the agent's capability.
 *
 * The api mock above deliberately no longer exports `createPR` / `getPRPreview`, so
 * re-importing either of them into the page fails this file at module load.
 */
describe('AtxTransformPage actions', () => {
  it('offers no Create PR button or dialog', async () => {
    renderPage();

    await screen.findByRole('option', { name: /^pom\.xml/ });

    expect(screen.queryByRole('button', { name: /create pr/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText(/create pull request/i)).not.toBeInTheDocument();
  });

  it('leaves Download Code as the page action', async () => {
    renderPage();

    expect(await screen.findByRole('button', { name: /download code/i })).toBeEnabled();
  });
});
