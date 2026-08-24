import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import type { SSEEvent } from '../types';

/**
 * Refresh-survival behaviour of the ATX Analysis console.
 *
 * The reported defect: with an analysis running, reloading the browser left the
 * console stuck on "Waiting for events...". These tests pin the three UI-side
 * halves of the fix:
 *
 * - the page re-attaches to the selected/running conversation on MOUNT, not
 *   only when a conversation is clicked;
 * - Cancel is offered for a reconnected running conversation and cancels the
 *   real conversation id;
 * - a stream that cannot be attached to surfaces as an error, never as an
 *   indefinite empty console.
 */

const streamAtxConversation = vi.fn();
const cancelAtxAnalysis = vi.fn();
const getAtxConversations = vi.fn();

vi.mock('../services/api', () => ({
  startAtxAnalysis: vi.fn(() => new AbortController()),
  streamAtxConversation: (...args: unknown[]) => streamAtxConversation(...args),
  cancelAtxAnalysis: (...args: unknown[]) => cancelAtxAnalysis(...args),
  getAtxConversations: () => getAtxConversations(),
  getAtxConversationDocs: vi.fn(async () => ({ docs: [] })),
}));

// Imported after the mock so the page picks up the stubbed api module.
const { AtxAnalysisPage } = await import('./AtxAnalysisPage');

const RUNNING_ID = 'atx_20250101_000000_running1';

function emitting(events: SSEEvent[]) {
  return (_id: string, onEvent: (event: SSEEvent) => void) => {
    events.forEach(onEvent);
    return new AbortController();
  };
}

beforeEach(() => {
  localStorage.clear();
  streamAtxConversation.mockReset();
  cancelAtxAnalysis.mockReset();
  getAtxConversations.mockReset();
  getAtxConversations.mockResolvedValue([
    { conversation_id: RUNNING_ID, status: 'running', created_at: '2025-01-01T00:00:00Z' },
  ]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AtxAnalysisPage refresh survival', () => {
  it('attaches to the running conversation on mount and restores its output', async () => {
    streamAtxConversation.mockImplementation(
      emitting([
        { type: 'init', conversation_id: RUNNING_ID } as SSEEvent,
        { type: 'log', data: 'agent: reading pom.xml' } as SSEEvent,
        { type: 'output', data: 'ATX CLI starting' } as SSEEvent,
      ])
    );

    render(<AtxAnalysisPage />);

    // Mount alone must trigger the reconnect — no click involved.
    await waitFor(() => expect(streamAtxConversation).toHaveBeenCalledTimes(1));
    expect(streamAtxConversation.mock.calls[0][0]).toBe(RUNNING_ID);

    // Replayed output is rendered instead of the empty-console placeholder.
    expect(await screen.findByText('agent: reading pom.xml')).toBeInTheDocument();
    expect(screen.getByText('ATX CLI starting')).toBeInTheDocument();
    expect(screen.queryByText(/Waiting for events/i)).not.toBeInTheDocument();
  });

  it('offers Cancel for a reconnected running conversation and cancels that id', async () => {
    streamAtxConversation.mockImplementation(
      emitting([{ type: 'log', data: 'agent: still working' } as SSEEvent])
    );
    cancelAtxAnalysis.mockResolvedValue(undefined);

    render(<AtxAnalysisPage />);

    const cancelButton = await screen.findByRole('button', { name: /cancel/i });
    fireEvent.click(cancelButton);

    await waitFor(() => expect(cancelAtxAnalysis).toHaveBeenCalledWith(RUNNING_ID));
  });

  it('renders a stream failure instead of an indefinite empty console', async () => {
    streamAtxConversation.mockImplementation(
      emitting([{ type: 'error', message: 'HTTP 404' } as SSEEvent])
    );

    render(<AtxAnalysisPage />);

    expect(await screen.findByText(/Could not attach to conversation/i)).toBeInTheDocument();
    expect(screen.getByText(/HTTP 404/)).toBeInTheDocument();
    expect(screen.queryByText(/Waiting for events/i)).not.toBeInTheDocument();
    // A dead stream must not leave a Cancel button implying live work.
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
  });
});
