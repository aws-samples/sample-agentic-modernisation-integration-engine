import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { DiscoveryReport } from '../types/dbDiscovery';

const getDbDiscovery = vi.fn();
const downloadDbDiscoveryMarkdown = vi.fn();
vi.mock('../services/api', () => ({
  getDbDiscovery: (id: string) => getDbDiscovery(id),
  downloadDbDiscoveryMarkdown: (id: string) => downloadDbDiscoveryMarkdown(id),
  saveBlob: vi.fn(),
}));

const { DatabaseDiscoveryResults, FindingsTab, ObjectsTab } = await import('./DatabaseDiscoveryResults');

const counts = { blocker: 0, high: 0, medium: 0, low: 0, info: 0 };

const REPORT: DiscoveryReport = {
  discovery_id: 'dbgithub_20261103_120000',
  source_type: 'github',
  source_url: 'https://github.com/microsoft/sql-server-samples',
  branch: 'master',
  subpath: 'samples/databases/wide-world-importers/wwi-ssdt',
  target: 'Amazon Aurora MySQL 3 (MySQL 8.0 compatible)',
  completed_at: '2026-11-03T12:00:05Z',
  summary: {
    files_scanned: { sql: 2, application: 1, skipped_too_large: [] },
    object_counts: { procedure: 1, table: 1 },
    finding_counts: { blocker: 0, high: 2, medium: 1, low: 0, info: 1 },
    finding_counts_by_source: { database: 3, application: 1 },
    category_counts: { syntax: 2, feature: 1, application: 1 },
    effort_counts: { low: 0, medium: 1, high: 1, very_high: 0 },
    effort_formula: 'points = …',
    objects_needing_manual_work: 1,
    top_rules: [
      { rule_id: 'SYN_MERGE', title: 'MERGE statement', severity: 'high', category: 'syntax', occurrences: 1, objects: 1 },
    ],
    schemas: ['Sales', 'Website'],
    total_sql_lines: 42,
    ast_parse: { batches: 3, parsed: 1 },
    logic_distribution: {
      database_modules: 1,
      database_module_lines: 30,
      embedded_sql_statements: 1,
      dynamic_embedded_sql_statements: 1,
    },
    application: {
      files_with_findings: 1,
      embedded_sql_statements: 1,
      stored_procedure_calls: 2,
      unresolved_procedure_calls: 1,
      procedures_called_from_code: 1,
      procedures_not_called_from_code: 0,
      connection_strings: 1,
      technologies: ['ADO.NET (System.Data.SqlClient)'],
    },
  },
  objects: [
    {
      id: 'procedure:website.upsertorders',
      schema: 'Website',
      name: 'UpsertOrders',
      type: 'procedure',
      subtype: 'sql',
      file: 'Website/UpsertOrders.sql',
      line: 1,
      end_line: 30,
      lines_of_code: 30,
      parent: '',
      alias_base_type: '',
      columns: [],
      references: ['table:sales.orders'],
      referenced_by: [],
      called_from_code: ['src/Repo.cs:12'],
      ast_parsed: false,
      finding_counts: { ...counts, high: 1 },
      effort_points: 9.3,
      effort: 'high',
    },
    {
      id: 'table:sales.orders',
      schema: 'Sales',
      name: 'Orders',
      type: 'table',
      subtype: 'temporal',
      file: 'Sales/Orders.sql',
      line: 1,
      end_line: 12,
      lines_of_code: 12,
      parent: '',
      alias_base_type: '',
      columns: [
        {
          name: 'OrderID',
          sql_type: 'int',
          nullable: false,
          identity: false,
          computed: false,
          persisted: false,
          has_default: true,
          default_sequence: true,
          generated_always: false,
          mysql_type: 'INT',
          mapping_note: '',
        },
        {
          name: 'ValidFrom',
          sql_type: 'datetime2(7)',
          nullable: false,
          identity: false,
          computed: false,
          persisted: false,
          has_default: false,
          default_sequence: false,
          generated_always: true,
          mysql_type: 'DATETIME(6)',
          mapping_note: 'precision 7 → 6',
        },
      ],
      references: [],
      referenced_by: ['procedure:website.upsertorders'],
      called_from_code: [],
      ast_parsed: true,
      finding_counts: { ...counts, high: 1, medium: 1 },
      effort_points: 7.1,
      effort: 'medium',
    },
  ],
  findings: [
    {
      id: 'F00001',
      rule_id: 'SYN_MERGE',
      title: 'MERGE statement',
      category: 'syntax',
      severity: 'high',
      source: 'database',
      object_id: 'procedure:website.upsertorders',
      file: 'Website/UpsertOrders.sql',
      line: 7,
      occurrences: 1,
      lines: [7],
      snippet: 'MERGE Sales.Orders AS t',
      recommendation: 'Rewrite as INSERT … ON DUPLICATE KEY UPDATE.',
    },
    {
      id: 'F00002',
      rule_id: 'FEAT_TEMPORAL',
      title: 'System-versioned temporal table',
      category: 'feature',
      severity: 'high',
      source: 'database',
      object_id: 'table:sales.orders',
      file: 'Sales/Orders.sql',
      line: 9,
      occurrences: 2,
      lines: [9, 11],
      snippet: 'WITH (SYSTEM_VERSIONING = ON)',
      recommendation: 'Keep the history table and maintain it with triggers.',
    },
    {
      id: 'F00003',
      rule_id: 'DT_DATETIME2_7',
      title: 'DATETIME2 with 7-digit precision',
      category: 'syntax',
      severity: 'medium',
      source: 'database',
      object_id: 'table:sales.orders',
      file: 'Sales/Orders.sql',
      line: 4,
      occurrences: 1,
      lines: [4],
      snippet: '[ValidFrom] DATETIME2 (7)',
      recommendation: 'Use DATETIME(6).',
    },
    {
      id: 'F00004',
      rule_id: 'PLAT_EXTENDED_PROPERTY',
      title: 'Extended property',
      category: 'application',
      severity: 'info',
      source: 'application',
      object_id: 'file:src/Repo.cs',
      file: 'src/Repo.cs',
      line: 3,
      occurrences: 1,
      lines: [3],
      snippet: '',
      recommendation: 'Move into COMMENT clauses.',
    },
  ],
  type_mappings: [{ sql_server_type: 'datetime2(7)', mysql_type: 'DATETIME(6)', note: 'precision 7 → 6', column_count: 1 }],
  application: {
    files: [{ file: 'src/Repo.cs', technologies: ['ADO.NET (System.Data.SqlClient)'], embedded_sql_count: 1, finding_count: 1 }],
    embedded_sql: [
      {
        id: 'sql:src/Repo.cs:20:0',
        file: 'src/Repo.cs',
        line: 20,
        sql: "SELECT TOP 10 * FROM Sales.Orders WHERE Name = '{?}'",
        interpolated: true,
        concatenated: false,
        dynamic: true,
        rule_ids: ['SYN_TOP', 'APP_SQL_INJECTION'],
      },
    ],
    stored_procedure_calls: [
      { file: 'src/Repo.cs', line: 12, procedure: 'Website.UpsertOrders', object_id: 'procedure:website.upsertorders', via: 'command_type', resolved: true },
      { file: 'src/Repo.cs', line: 30, procedure: 'Sales.Retired', object_id: null, via: 'command_type', resolved: false },
    ],
    connection_strings: [
      {
        file: 'src/Web.config',
        line: 5,
        name: 'Main',
        connection_string: 'Server=x;Database=WWI;User ID=u;Password=***',
        server: 'x',
        database: 'WWI',
        provider: 'sqlserver',
        windows_auth: false,
        mars: true,
        password_present: true,
      },
    ],
    packages: [{ name: 'EntityFramework', version: '6.4.4', file: 'packages.config', replacement: 'Pomelo.EntityFrameworkCore.MySql', source: 'package' }],
    target_frameworks: [{ file: 'src/App.csproj', framework: 'v4.8' }],
    technologies: [{ name: 'ADO.NET (System.Data.SqlClient)', files: 1 }],
  },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/database-discovery/${REPORT.discovery_id}`]}>
      <Routes>
        <Route path="/database-discovery/:id" element={<DatabaseDiscoveryResults />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('DatabaseDiscoveryResults', () => {
  beforeEach(() => {
    getDbDiscovery.mockReset();
    downloadDbDiscoveryMarkdown.mockReset();
  });

  it('loads the report by route id and shows the overview', async () => {
    getDbDiscovery.mockResolvedValue(REPORT);
    renderPage();
    expect(await screen.findByText(/SQL Server → Amazon Aurora MySQL 3/)).toBeInTheDocument();
    expect(getDbDiscovery).toHaveBeenCalledWith(REPORT.discovery_id);
    expect(screen.getByTestId('severity-count-high')).toHaveTextContent('2');
    expect(screen.getByText('MERGE statement')).toBeInTheDocument();
    expect(screen.getByText(/sqlglot parsed 1 of 3/)).toBeInTheDocument();
    expect(screen.getByText(/1 procedure calls in code name no procedure/)).toBeInTheDocument();
  });

  it('shows a not-found message for an unknown discovery', async () => {
    getDbDiscovery.mockRejectedValue({ response: { status: 404 } });
    renderPage();
    expect(await screen.findByText('Discovery not found.')).toBeInTheDocument();
  });

  it('switches through every tab', async () => {
    getDbDiscovery.mockResolvedValue(REPORT);
    renderPage();
    await screen.findByText(/SQL Server → Amazon Aurora MySQL 3/);

    fireEvent.click(screen.getByRole('tab', { name: 'Objects (2)' }));
    expect(screen.getByRole('table', { name: 'objects' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Findings (4)' }));
    expect(screen.getByTestId('finding-count')).toHaveTextContent('3 findings');

    fireEvent.click(screen.getByRole('tab', { name: 'Application SQL' }));
    expect(screen.getByRole('table', { name: 'connection strings' })).toHaveTextContent('Password=***');
    expect(screen.getByText('not in scripts')).toBeInTheDocument();
    expect(screen.getByText('Pomelo.EntityFrameworkCore.MySql')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Type Mapping' }));
    expect(screen.getByRole('table', { name: 'type mapping' })).toHaveTextContent('DATETIME(6)');
  });

  it('opens the object drawer with columns, findings and references', async () => {
    getDbDiscovery.mockResolvedValue(REPORT);
    renderPage();
    await screen.findByText(/SQL Server → Amazon Aurora MySQL 3/);
    fireEvent.click(screen.getByRole('tab', { name: 'Objects (2)' }));
    fireEvent.click(screen.getByText('Sales.Orders'));
    const drawer = await screen.findByRole('presentation');
    expect(within(drawer).getByRole('table', { name: 'columns' })).toHaveTextContent('DATETIME(6)');
    expect(within(drawer).getByText('System-versioned temporal table')).toBeInTheDocument();
    // Following a "referenced by" chip navigates the drawer to that object.
    fireEvent.click(within(drawer).getByText('procedure: Website.UpsertOrders'));
    await waitFor(() => expect(within(drawer).getByText('src/Repo.cs:12')).toBeInTheDocument());
  });

  it('keeps migration steps off the report and links to them', async () => {
    getDbDiscovery.mockResolvedValue(REPORT);
    renderPage();
    await screen.findByText(/SQL Server → Amazon Aurora MySQL 3/);
    expect(screen.queryByRole('tab', { name: 'Conversion' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Validation' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Continue to migration/ })).toBeInTheDocument();
  });

  it('requests the markdown download for this discovery', async () => {
    getDbDiscovery.mockResolvedValue(REPORT);
    downloadDbDiscoveryMarkdown.mockResolvedValue(undefined);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Markdown report/ }));
    expect(downloadDbDiscoveryMarkdown).toHaveBeenCalledWith(REPORT.discovery_id);
  });
});

describe('ObjectsTab', () => {
  it('sorts by effort and filters by search text', () => {
    render(<ObjectsTab report={REPORT} onOpen={() => {}} />);
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('Website.UpsertOrders');
    fireEvent.change(screen.getByLabelText('Search objects'), { target: { value: 'sales/' } });
    expect(screen.getByTestId('object-count')).toHaveTextContent('1 objects');
  });
});

describe('FindingsTab', () => {
  it('hides info findings by default and filters by search', () => {
    render(<FindingsTab report={REPORT} onOpen={() => {}} />);
    expect(screen.getByTestId('finding-count')).toHaveTextContent('3 findings');
    expect(screen.queryByText('Extended property')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search findings'), { target: { value: 'merge' } });
    expect(screen.getByTestId('finding-count')).toHaveTextContent('1 findings');
  });
});
