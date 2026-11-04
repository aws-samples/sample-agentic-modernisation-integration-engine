import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { DiscoveryReport, ValidationStatus } from '../types/dbDiscovery';

const getDbValidationStatus = vi.fn();
const getDbValidationRuns = vi.fn();
const getDbValidationRun = vi.fn();
const startDbValidation = vi.fn();
vi.mock('../services/api', () => ({
  getDbValidationStatus: () => getDbValidationStatus(),
  getDbValidationRuns: () => getDbValidationRuns(),
  getDbValidationRun: (id: string) => getDbValidationRun(id),
  startDbValidation: (...a: unknown[]) => startDbValidation(...a),
  startDbCopy: vi.fn(),
}));

const { DbValidationPanel, parseProcedureTests, tablesUsedBy } = await import('./DbValidationPanel');

const REPORT = {
  discovery_id: 'dbupload_1',
  objects: [{ id: 'table:sales.t', schema: 'Sales', name: 'T', type: 'table', subtype: 'standard', columns: [] }],
} as unknown as DiscoveryReport;

const NOT_CONFIGURED: ValidationStatus = {
  source: { engine: 'sqlserver', configured: false, reachable: false, error: 'SQL Server source is not configured' },
  target: { engine: 'mysql', configured: true, reachable: true, host: 'mysql', port: 3306, user: 'root', sandbox: true, version: '8.0.46' },
  copy_enabled: false,
};

describe('DbValidationPanel', () => {
  beforeEach(() => {
    [getDbValidationStatus, getDbValidationRuns, getDbValidationRun, startDbValidation].forEach((m) => m.mockReset());
    getDbValidationRuns.mockResolvedValue([]);
  });

  it('shows connection status and disables runs without a source', async () => {
    getDbValidationStatus.mockResolvedValue(NOT_CONFIGURED);
    render(<DbValidationPanel report={REPORT} />);
    expect(await screen.findByTestId('sqlserver-status')).toHaveTextContent('not configured');
    expect(screen.getByTestId('mysql-status')).toHaveTextContent('reachable');
    expect(screen.getByRole('button', { name: 'Run validation' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /Copy test data/ })).not.toBeInTheDocument();
  });

  it('starts a run and shows table and procedure results', async () => {
    getDbValidationStatus.mockResolvedValue({
      ...NOT_CONFIGURED,
      source: { engine: 'sqlserver', configured: true, reachable: true, host: 'rds', port: 1433, user: 'u', database: 'WWI' },
    });
    startDbValidation.mockResolvedValue({ run_id: 'val_1' });
    getDbValidationRun.mockResolvedValue({
      run_id: 'val_1',
      kind: 'validation',
      discovery_id: 'dbupload_1',
      status: 'completed',
      progress: 100,
      message: '',
      started_at: '',
      completed_at: '',
      result: {
        tables: [
          { object_id: 'table:sales.t', table: 'Sales.T', columns_compared: 2, excluded_columns: [{ column: 'Loc', reason: 'x' }],
            source_rows: 5, target_rows: 4, source_checksum: 'aaaaaaaaaaaaaaaa', target_checksum: 'bbbbbbbbbbbbbbbb', status: 'mismatch', error: '' },
        ],
        procedures: [{ procedure: 'Sales.p', params: [], status: 'match', error: '', details: [] }],
      },
    });
    render(<DbValidationPanel report={REPORT} />);
    const run = await screen.findByRole('button', { name: 'Run validation' });
    await waitFor(() => expect(run).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText('procedure tests'), { target: { value: '[{"procedure":"Sales.p","params":[]}]' } });
    fireEvent.click(run);
    await waitFor(() => expect(startDbValidation).toHaveBeenCalledWith('dbupload_1', [], [{ procedure: 'Sales.p', params: [] }]));
    expect(await screen.findByText('mismatch')).toBeInTheDocument();
    expect(screen.getByText('not compared: Loc')).toBeInTheDocument();
    expect(screen.getByText('identical result sets')).toBeInTheDocument();
  });

  it('reports invalid procedure-test JSON without calling the API', async () => {
    getDbValidationStatus.mockResolvedValue({
      ...NOT_CONFIGURED,
      source: { engine: 'sqlserver', configured: true, reachable: true },
    });
    render(<DbValidationPanel report={REPORT} />);
    const run = await screen.findByRole('button', { name: 'Run validation' });
    await waitFor(() => expect(run).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText('procedure tests'), { target: { value: '[{bad' } });
    fireEvent.click(run);
    expect(await screen.findByText(/not valid JSON/)).toBeInTheDocument();
    expect(startDbValidation).not.toHaveBeenCalled();
  });

  it('parseProcedureTests', () => {
    expect(parseProcedureTests('')).toEqual([]);
    expect(() => parseProcedureTests('{}')).toThrow();
  });
});

describe('tablesUsedBy', () => {
  it('collects direct tables and tables reached through views and functions', () => {
    const report = {
      objects: [
        { id: 'procedure:a.p', type: 'procedure', references: ['table:a.t1', 'view:a.v', 'procedure:a.p'] },
        { id: 'view:a.v', type: 'view', references: ['table:a.t2', 'function:a.f'] },
        { id: 'function:a.f', type: 'function', references: ['table:a.t3'] },
        { id: 'table:a.t1', type: 'table', references: [] },
        { id: 'table:a.t2', type: 'table', references: [] },
        { id: 'table:a.t3', type: 'table', references: [] },
        { id: 'table:a.unused', type: 'table', references: [] },
      ],
    } as unknown as DiscoveryReport;
    expect(tablesUsedBy(report, 'procedure:a.p')).toEqual(['table:a.t1', 'table:a.t2', 'table:a.t3']);
  });
});

describe('DbValidationPanel statuses', () => {
  it('labels empty targets and tables missing from the source clearly', async () => {
    getDbValidationStatus.mockResolvedValue({
      ...NOT_CONFIGURED,
      source: { engine: 'sqlserver', configured: true, reachable: true },
    });
    getDbValidationRuns.mockResolvedValue([
      { run_id: 'val_2', kind: 'validation', discovery_id: 'dbupload_1', status: 'completed', progress: 100,
        message: '', started_at: '', completed_at: '', summary: { match: 1, not_loaded: 2, missing_in_source: 1 } },
    ]);
    getDbValidationRun.mockResolvedValue({
      run_id: 'val_2', kind: 'validation', discovery_id: 'dbupload_1', status: 'completed', progress: 100,
      message: '', started_at: '', completed_at: '',
      result: { tables: [
        { object_id: 'table:sales.t', table: 'Sales.T', columns_compared: 1, excluded_columns: [], source_rows: 5,
          target_rows: 0, source_checksum: 'a', target_checksum: '0', status: 'not_loaded', error: 'Target table is empty' },
        { object_id: 'table:dbo.x', table: 'dbo.X', columns_compared: 1, excluded_columns: [], source_rows: null,
          target_rows: null, source_checksum: '', target_checksum: '', status: 'missing_in_source', error: 'does not exist' },
      ], procedures: [] },
    });
    render(<DbValidationPanel report={REPORT} />);
    expect(await screen.findByText('not loaded 2')).toBeInTheDocument();
    expect(screen.getByText('missing in source 1')).toBeInTheDocument();
    fireEvent.click(screen.getByText('val_2'));
    expect(await screen.findByText('not loaded')).toBeInTheDocument();
    expect(screen.getByText('missing in source')).toBeInTheDocument();
  });
});
