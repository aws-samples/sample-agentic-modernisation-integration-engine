import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import type { ConversionRecord, DiscoveryReport } from '../types/dbDiscovery';

const getDbConversions = vi.fn();
const startDbConversions = vi.fn();
const reviewDbConversion = vi.fn();
const setDbConversionTests = vi.fn();
vi.mock('../services/api', () => ({
  getDbConversions: (id: string) => getDbConversions(id),
  startDbConversions: (id: string, ids: string[]) => startDbConversions(id, ids),
  reviewDbConversion: (...a: unknown[]) => reviewDbConversion(...a),
  setDbConversionTests: (...a: unknown[]) => setDbConversionTests(...a),
  downloadDbSchemaSql: vi.fn(() => Promise.resolve()),
  downloadDbConversionsSql: vi.fn(() => Promise.resolve()),
}));

const { DbConversionPanel } = await import('./DbConversionPanel');

const counts = { blocker: 0, high: 1, medium: 0, low: 0, info: 0 };
const obj = (id: string, name: string, type: string, extra = {}) => ({
  id,
  schema: 'Website',
  name,
  type,
  subtype: 'sql',
  file: 'x.sql',
  line: 1,
  end_line: 9,
  lines_of_code: 9,
  parent: '',
  alias_base_type: '',
  columns: [],
  references: [],
  referenced_by: [],
  called_from_code: [],
  ast_parsed: false,
  finding_counts: counts,
  effort_points: 5,
  effort: 'medium' as const,
  source: `CREATE PROCEDURE Website.${name} AS SELECT TOP 5 1`,
  ...extra,
});

const REPORT = {
  discovery_id: 'dbupload_1',
  objects: [
    obj('procedure:website.a', 'A', 'procedure'),
    obj('procedure:website.b', 'B', 'procedure'),
    obj('table:sales.t', 'T', 'table'),
  ],
  findings: [],
} as unknown as DiscoveryReport;

const converted: ConversionRecord = {
  object_id: 'procedure:website.a',
  object_type: 'procedure',
  object_name: 'Website.A',
  status: 'converted',
  attempts: [
    {
      n: 1,
      sql: 'CREATE PROCEDURE Website.A() BEGIN SELECT 1 LIMIT 5; END',
      notes: '- TOP → LIMIT',
      compile: { ok: true, errno: null, message: '' },
      judge: { scores: { semantic_equivalence: 5, completeness: 5 }, verdict: 'pass', issues: [] },
      behaviour: {
        status: 'passed',
        tests: [
          { name: 'normal search', params: [{ name: 'SearchText', value: 'Toys' }], origin: 'generated', status: 'match',
            details: [], source_error: '', target_error: '', result_rows: { source: [3], target: [3] } },
        ],
      },
      passed: true,
    },
  ],
  final_sql: 'CREATE PROCEDURE Website.A() BEGIN SELECT 1 LIMIT 5; END',
  notes: '- TOP → LIMIT',
  reviewer_note: '',
  edited: false,
  error: '',
  behaviour_status: 'passed',
  updated_at: '',
};

describe('DbConversionPanel behaviour tests', () => {
  beforeEach(() => {
    [getDbConversions, setDbConversionTests].forEach((m) => m.mockReset());
    getDbConversions.mockResolvedValue({ conversions: [converted], sandbox: { configured: true, reachable: true, version: '8.0.46' } });
  });

  it('shows the behaviour result in the list and the test table in review', async () => {
    render(<DbConversionPanel report={REPORT} />);
    expect(await screen.findByText('Same results as SQL Server')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'review Website.A' }));
    const dialog = await screen.findByRole('dialog');
    const table = within(dialog).getAllByRole('table', { name: 'behaviour tests' })[0];
    expect(table).toHaveTextContent('normal search');
    expect(table).toHaveTextContent('SearchText="Toys"');
    expect(table).toHaveTextContent('same');
  });

  it('saves reviewer test cases without closing the dialog', async () => {
    setDbConversionTests.mockResolvedValue({ ...converted, custom_tests: [{ name: 'x', params: [] }] });
    render(<DbConversionPanel report={REPORT} />);
    fireEvent.click(await screen.findByRole('button', { name: 'review Website.A' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('custom tests'), { target: { value: '[{"name":"x","params":[]}]' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save tests' }));
    await waitFor(() => expect(setDbConversionTests).toHaveBeenCalledWith('dbupload_1', 'procedure:website.a', [{ name: 'x', params: [] }]));
    expect(await within(dialog).findByRole('button', { name: 'Saved' })).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('custom tests'), { target: { value: '[{bad' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save tests' }));
    expect(await within(dialog).findByText(/not valid JSON/)).toBeInTheDocument();
  });
});

describe('DbConversionPanel re-convert', () => {
  beforeEach(() => {
    [getDbConversions, startDbConversions].forEach((m) => m.mockReset());
    getDbConversions.mockResolvedValue({ conversions: [converted], sandbox: { configured: true, reachable: true, version: '8.0.46' } });
    startDbConversions.mockResolvedValue({ queued: [{ object_id: 'procedure:website.a' }], skipped: [] });
  });

  it('re-converts a converted object from its row', async () => {
    render(<DbConversionPanel report={REPORT} />);
    fireEvent.click(await screen.findByRole('button', { name: 're-convert Website.A' }));
    await waitFor(() => expect(startDbConversions).toHaveBeenCalledWith('dbupload_1', ['procedure:website.a']));
    expect(await screen.findByText(/Re-converting Website.A/)).toBeInTheDocument();
  });

  it('re-converts from the review dialog, confirming first when approved', async () => {
    getDbConversions.mockResolvedValue({
      conversions: [{ ...converted, status: 'approved' }],
      sandbox: { configured: true, reachable: true, version: '8.0.46' },
    });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<DbConversionPanel report={REPORT} />);
    fireEvent.click(await screen.findByRole('button', { name: 'review Website.A' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Re-convert' }));
    expect(startDbConversions).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Re-convert' }));
    await waitFor(() => expect(startDbConversions).toHaveBeenCalledWith('dbupload_1', ['procedure:website.a']));
    expect(confirm).toHaveBeenCalledTimes(2);
    confirm.mockRestore();
  });

  it('does not offer re-convert while a conversion is running', async () => {
    getDbConversions.mockResolvedValue({
      conversions: [{ ...converted, status: 'converting' }],
      sandbox: { configured: true, reachable: true, version: '8.0.46' },
    });
    render(<DbConversionPanel report={REPORT} />);
    await screen.findByText('Converting…');
    expect(screen.queryByRole('button', { name: 're-convert Website.A' })).not.toBeInTheDocument();
  });
});

describe('DbConversionPanel', () => {
  beforeEach(() => {
    [getDbConversions, startDbConversions, reviewDbConversion].forEach((m) => m.mockReset());
    getDbConversions.mockResolvedValue({ conversions: [converted], sandbox: { configured: true, reachable: true, version: '8.0.46' } });
  });

  it('lists only convertible objects with their conversion status and sandbox state', async () => {
    render(<DbConversionPanel report={REPORT} />);
    expect(await screen.findByText('MySQL sandbox 8.0.46')).toBeInTheDocument();
    const table = screen.getByRole('table', { name: 'convertible objects' });
    expect(within(table).getByText('Website.A')).toBeInTheDocument();
    expect(within(table).queryByText('Website.T')).not.toBeInTheDocument();
    expect(within(table).getByText('Checks passed')).toBeInTheDocument();
  });

  it('queues the selected objects', async () => {
    startDbConversions.mockResolvedValue({ queued: [{ object_id: 'procedure:website.b' }], skipped: [] });
    render(<DbConversionPanel report={REPORT} />);
    await screen.findByText('MySQL sandbox 8.0.46');
    fireEvent.click(screen.getByRole('checkbox', { name: 'select Website.B' }));
    fireEvent.click(screen.getByRole('button', { name: 'Convert selected (1)' }));
    await waitFor(() => expect(startDbConversions).toHaveBeenCalledWith('dbupload_1', ['procedure:website.b']));
    expect(await screen.findByText(/Queued 1 object/)).toBeInTheDocument();
  });

  it('opens the review dialog side by side and approves', async () => {
    reviewDbConversion.mockResolvedValue({ ...converted, status: 'approved' });
    render(<DbConversionPanel report={REPORT} />);
    fireEvent.click(await screen.findByRole('button', { name: 'review Website.A' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByTestId('source-sql')).toHaveTextContent('SELECT TOP 5 1');
    expect(within(dialog).getByLabelText('converted sql')).toHaveValue(converted.final_sql);
    expect(within(dialog).getByText('compiles in MySQL 8.0')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(reviewDbConversion).toHaveBeenCalledWith('dbupload_1', 'procedure:website.a', 'approve', '', ''));
  });

  it('an edit is sent for compilation and a compile error is shown', async () => {
    reviewDbConversion.mockRejectedValue({ response: { data: { detail: 'Edited SQL does not compile in MySQL 8.0: error 1064' } } });
    render(<DbConversionPanel report={REPORT} />);
    fireEvent.click(await screen.findByRole('button', { name: 'review Website.A' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('converted sql'), { target: { value: 'CREATE PROCEDURE Website.A() BEGIN BAD; END' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Compile edit/ }));
    expect(await within(dialog).findByText(/does not compile/)).toBeInTheDocument();
    expect(reviewDbConversion).toHaveBeenCalledWith('dbupload_1', 'procedure:website.a', 'edit', 'CREATE PROCEDURE Website.A() BEGIN BAD; END', '');
  });
});
