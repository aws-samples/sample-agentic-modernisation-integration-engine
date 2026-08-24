import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import type { TransformationDefinition } from '../types';

/**
 * The transformation dropdown must submit the ATX *identifier*, not the display label.
 *
 * The reported defect: every transformation failed with
 * `ValidationException ... Value at 'resource' failed to satisfy constraint` because the
 * dropdown was bound to `t.name` ("Java Version Upgrade") while the ATX CLI needs
 * `t.id` ("AWS/java-version-upgrade") — the space fails the service-side pattern.
 *
 * Asserted on the request body, which is the seam that actually broke, plus the label
 * still being what the user reads.
 */

const startTransformation = vi.fn();
const getTransformations = vi.fn();
const getTransformationHistory = vi.fn();

vi.mock('../services/api', () => ({
  startTransformation: (...args: unknown[]) => startTransformation(...args),
  getTransformations: () => getTransformations(),
  getTransformationHistory: () => getTransformationHistory(),
  streamTransformConversation: vi.fn(() => new AbortController()),
}));

const { AtxJavaTransformPage } = await import('./AtxJavaTransformPage');

/**
 * The page navigates (completed history records lead to their results page), so it
 * needs a router. The stand-in results route lets a test assert the destination
 * without pulling in the real results page and its own data fetching.
 */
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/atx-transform']}>
      <Routes>
        <Route path="/atx-transform" element={<AtxJavaTransformPage />} />
        <Route path="/transform-results/:id" element={<div>results page stub</div>} />
      </Routes>
    </MemoryRouter>
  );
}

const AWS_MANAGED: TransformationDefinition = {
  id: 'AWS/java-version-upgrade',
  name: 'Java Version Upgrade',
  description: 'Upgrade Java applications',
  type: 'aws-managed',
  definition_path: '',
  published: true,
  atx_definition_name: 'AWS/java-version-upgrade',
};

const CUSTOM: TransformationDefinition = {
  // A custom record's id is a local uuid4 the ATX CLI has never heard of; its
  // registered ATX name is what the agent resolves into atx_definition_name.
  id: '93ae1efc-b409-4500-b007-074e79381ba8',
  name: 'e2e-test-transform',
  description: 'E2E test transformation definition',
  type: 'custom',
  definition_path: '',
  published: false,
  atx_definition_name: 'e2e-test-transform',
};

async function selectAndStart(optionLabel: string) {
  renderPage();

  fireEvent.change(await screen.findByLabelText('Repository URL'), {
    target: { value: 'https://github.com/example/repo' },
  });

  fireEvent.mouseDown(screen.getByRole('combobox', { name: /transformation type/i }));
  fireEvent.click(await screen.findByRole('option', { name: optionLabel }));
  fireEvent.click(screen.getByRole('button', { name: /start/i }));

  await waitFor(() => expect(startTransformation).toHaveBeenCalled());
  return startTransformation.mock.calls[0];
}

beforeEach(() => {
  startTransformation.mockReset();
  getTransformations.mockReset();
  getTransformationHistory.mockReset();
  startTransformation.mockResolvedValue({ repo_id: 'abc123', status: 'running' });
  getTransformationHistory.mockResolvedValue([]);
  getTransformations.mockResolvedValue([AWS_MANAGED, CUSTOM]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AtxJavaTransformPage transformation identifier', () => {
  it('submits the AWS-managed identifier, not the display label', async () => {
    const call = await selectAndStart('Java Version Upgrade');

    expect(call[2]).toBe('AWS/java-version-upgrade');
    expect(call[2]).not.toBe('Java Version Upgrade');
  });

  it('still shows the human label in the dropdown', async () => {
    renderPage();
    fireEvent.mouseDown(
      await screen.findByRole('combobox', { name: /transformation type/i })
    );

    expect(await screen.findByRole('option', { name: 'Java Version Upgrade' })).toBeInTheDocument();
  });

  it("submits a custom definition's ATX name, not its local uuid", async () => {
    const call = await selectAndStart('e2e-test-transform');

    expect(call[2]).toBe('e2e-test-transform');
    expect(call[2]).not.toBe(CUSTOM.id);
  });

  it('falls back to id for an AWS-managed record with no resolved identifier', async () => {
    const { atx_definition_name: _omitted, ...withoutResolved } = AWS_MANAGED;
    getTransformations.mockResolvedValue([withoutResolved]);

    const call = await selectAndStart('Java Version Upgrade');

    expect(call[2]).toBe('AWS/java-version-upgrade');
  });

  it('does not offer a custom definition that has no valid ATX identifier', async () => {
    getTransformations.mockResolvedValue([
      { ...CUSTOM, name: 'not a valid atx name', atx_definition_name: null },
    ]);

    renderPage();
    fireEvent.mouseDown(
      await screen.findByRole('combobox', { name: /transformation type/i })
    );

    const option = await screen.findByRole('option', { name: 'not a valid atx name' });
    expect(option).toHaveAttribute('aria-disabled', 'true');
  });
});
/**
 * The results page at `/transform-results/:id` was routed in App.tsx but unreachable:
 * repo-wide, nothing navigated to it. A completed transformation must lead there —
 * that page is where the changed files and the code download live.
 */
describe('AtxJavaTransformPage history navigation', () => {
  const COMPLETED = {
    repo_id: 'abc123def456',
    status: 'completed',
    created_at: '2025-01-01T00:00:00Z',
    repo_url: 'https://github.com/example/legacy-app',
  };

  const RUNNING = {
    repo_id: 'running99',
    status: 'running',
    created_at: '2025-01-01T00:00:00Z',
    repo_url: 'https://github.com/example/in-flight',
  };

  it('navigates a completed transformation to its results page', async () => {
    getTransformationHistory.mockResolvedValue([COMPLETED]);

    renderPage();

    fireEvent.click(
      await screen.findByRole('button', { name: `View transform results for ${COMPLETED.repo_id}` })
    );

    expect(await screen.findByText('results page stub')).toBeInTheDocument();
  });

  it('keeps the console replay reachable for a completed transformation', async () => {
    getTransformationHistory.mockResolvedValue([COMPLETED]);

    renderPage();

    fireEvent.click(
      await screen.findByRole('button', { name: `Replay console for ${COMPLETED.repo_id}` })
    );

    // Stayed on the transform page rather than navigating away. Matched on the
    // heading role rather than the raw text: "ATX Transform" is also the sidebar
    // nav label, so a text-only match is ambiguous the moment the shell renders.
    expect(screen.getByRole('heading', { name: 'ATX Transform' })).toBeInTheDocument();
    expect(screen.queryByText('results page stub')).not.toBeInTheDocument();
  });

  it('replays the console for a transformation that has not completed', async () => {
    getTransformationHistory.mockResolvedValue([RUNNING]);

    renderPage();

    fireEvent.click(
      await screen.findByRole('button', { name: `Replay console for ${RUNNING.repo_id}` })
    );

    // Nothing to review yet, so no navigation.
    expect(screen.queryByText('results page stub')).not.toBeInTheDocument();
  });
});
