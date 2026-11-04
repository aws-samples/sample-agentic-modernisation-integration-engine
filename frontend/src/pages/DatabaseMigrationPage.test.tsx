import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const getDbDiscovery = vi.fn();
vi.mock('../services/api', () => ({ getDbDiscovery: (id: string) => getDbDiscovery(id) }));
vi.mock('../components/DbConversionPanel', () => ({ DbConversionPanel: () => <div>conversion-panel</div> }));
vi.mock('../components/DbValidationPanel', () => ({ DbValidationPanel: () => <div>validation-panel</div> }));
vi.mock('../components/DbDmsPanel', () => ({ DbDmsPanel: () => <div>dms-panel</div> }));

const { DatabaseMigrationPage } = await import('./DatabaseMigrationPage');

function renderAt(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/database-discovery/${id}/migration`]}>
      <Routes>
        <Route path="/database-discovery/:id/migration" element={<DatabaseMigrationPage />} />
        <Route path="/database-discovery/:id" element={<div>report-page</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('DatabaseMigrationPage', () => {
  beforeEach(() => {
    getDbDiscovery.mockReset();
  });

  it('loads the discovery and shows the migration steps, starting with conversion', async () => {
    getDbDiscovery.mockResolvedValue({ discovery_id: 'dbupload_1', target: 'Aurora MySQL', source_url: '', subpath: '', completed_at: 'now' });
    renderAt('dbupload_1');
    expect(await screen.findByText('conversion-panel')).toBeInTheDocument();
    expect(getDbDiscovery).toHaveBeenCalledWith('dbupload_1');
    expect(screen.getByText(/Based on discovery dbupload_1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Validation' }));
    expect(screen.getByText('validation-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'DMS' }));
    expect(screen.getByText('dms-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Discovery report/ }));
    expect(await screen.findByText('report-page')).toBeInTheDocument();
  });

  it('shows not found for an unknown discovery', async () => {
    getDbDiscovery.mockRejectedValue({ response: { status: 404 } });
    renderAt('dbupload_x');
    expect(await screen.findByText('Discovery not found.')).toBeInTheDocument();
  });
});
