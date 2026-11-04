import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const getDbDiscoveries = vi.fn();
const startDbDiscoveryGithub = vi.fn();
const startDbDiscoveryUpload = vi.fn();
const getDbDiscoveryStatus = vi.fn();
const deleteDbDiscovery = vi.fn();
const navigate = vi.fn();

vi.mock('../services/api', () => ({
  getDbDiscoveries: () => getDbDiscoveries(),
  startDbDiscoveryGithub: (...a: unknown[]) => startDbDiscoveryGithub(...a),
  startDbDiscoveryUpload: (...a: unknown[]) => startDbDiscoveryUpload(...a),
  getDbDiscoveryStatus: (id: string) => getDbDiscoveryStatus(id),
  deleteDbDiscovery: (id: string) => deleteDbDiscovery(id),
  getAnalysisStatus: vi.fn(),
}));
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}));

const { DatabaseDiscoveryPage, WWI_SAMPLE } = await import('./DatabaseDiscoveryPage');

function renderPage() {
  return render(
    <MemoryRouter>
      <DatabaseDiscoveryPage />
    </MemoryRouter>
  );
}

describe('DatabaseDiscoveryPage', () => {
  beforeEach(() => {
    [getDbDiscoveries, startDbDiscoveryGithub, startDbDiscoveryUpload, getDbDiscoveryStatus, deleteDbDiscovery, navigate].forEach(
      (m) => m.mockReset()
    );
    getDbDiscoveries.mockResolvedValue([]);
  });

  it('prefills the WideWorldImporters sample and starts a GitHub discovery with the sub-path', async () => {
    startDbDiscoveryGithub.mockResolvedValue({ discovery_id: 'dbgithub_1' });
    getDbDiscoveryStatus.mockResolvedValue({ status: 'processing', progress: 40, current_step: 'rules', message: 'Evaluating' });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Use WideWorldImporters sample' }));
    expect(screen.getByLabelText(/Repository URL/)).toHaveValue(WWI_SAMPLE.repoUrl);
    fireEvent.click(screen.getByRole('button', { name: 'Run Discovery' }));
    await waitFor(() =>
      expect(startDbDiscoveryGithub).toHaveBeenCalledWith(WWI_SAMPLE.repoUrl, 'master', WWI_SAMPLE.subpath, undefined)
    );
    expect(await screen.findByText('Discovery dbgithub_1')).toBeInTheDocument();
    expect(await screen.findByText('Evaluating')).toBeInTheDocument();
    expect(screen.getByText('MySQL rules')).toBeInTheDocument();
  });

  it('navigates to the results when the run completes', async () => {
    startDbDiscoveryGithub.mockResolvedValue({ discovery_id: 'dbgithub_2' });
    getDbDiscoveryStatus.mockResolvedValue({ status: 'completed', progress: 100, current_step: 'done', message: 'done' });
    renderPage();
    fireEvent.change(screen.getByLabelText(/Repository URL/), { target: { value: 'https://github.com/a/b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run Discovery' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/database-discovery/dbgithub_2'));
  });

  it('shows the backend error detail when a run fails to start', async () => {
    startDbDiscoveryGithub.mockRejectedValue({ response: { data: { detail: 'Invalid subpath' } } });
    renderPage();
    fireEvent.change(screen.getByLabelText(/Repository URL/), { target: { value: 'https://github.com/a/b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run Discovery' }));
    expect(await screen.findByText('Invalid subpath')).toBeInTheDocument();
  });

  it('shows a failed run message and returns to the form', async () => {
    startDbDiscoveryGithub.mockResolvedValue({ discovery_id: 'dbgithub_3' });
    getDbDiscoveryStatus.mockResolvedValue({ status: 'failed', progress: 2, current_step: 'error', message: "Path 'x' was not found" });
    renderPage();
    fireEvent.change(screen.getByLabelText(/Repository URL/), { target: { value: 'https://github.com/a/b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run Discovery' }));
    expect(await screen.findByText("Path 'x' was not found")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run Discovery' })).toBeInTheDocument();
  });

  it('lists previous discoveries, opens and deletes them', async () => {
    getDbDiscoveries.mockResolvedValue([
      {
        discovery_id: 'dbupload_20261103_101010',
        source_type: 'upload',
        source_url: '',
        subpath: '',
        completed_at: '2026-11-03T10:10:15Z',
        object_counts: { table: 3, procedure: 2 },
        finding_counts: { blocker: 1, high: 4, medium: 2 },
      },
    ]);
    deleteDbDiscovery.mockResolvedValue(undefined);
    renderPage();
    expect(await screen.findByText('dbupload_20261103_101010')).toBeInTheDocument();
    expect(screen.getByText('ZIP upload')).toBeInTheDocument();
    expect(screen.getByText('blocker 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'open dbupload_20261103_101010' }));
    expect(navigate).toHaveBeenCalledWith('/database-discovery/dbupload_20261103_101010');
    fireEvent.click(screen.getByRole('button', { name: 'delete dbupload_20261103_101010' }));
    await waitFor(() => expect(deleteDbDiscovery).toHaveBeenCalledWith('dbupload_20261103_101010'));
    expect(getDbDiscoveries).toHaveBeenCalledTimes(2);
  });
});
