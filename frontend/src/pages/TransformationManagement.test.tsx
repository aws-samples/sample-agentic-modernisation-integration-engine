import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { TransformationDefinition } from '../types';

/**
 * The reported defect: the AWS Managed tab always said "No AWS managed transformations
 * available." It read the *backend's* custom-definition CRUD collection
 * (`getTransformationDefinitions`) and filtered it by `type === 'aws-managed'` — but no
 * AWS-managed record is ever written to that collection, so the partition was empty by
 * construction. The 13 AWS-managed records live in the transform agent's catalog
 * (`getTransformations`).
 *
 * Asserted on the source each tab reads and on the empty-vs-failed distinction, which
 * is where the defect actually lives.
 */

const getTransformationDefinitions = vi.fn();
const getTransformations = vi.fn();

vi.mock('../services/api', () => ({
  getTransformationDefinitions: () => getTransformationDefinitions(),
  getTransformations: () => getTransformations(),
  createTransformationDefinition: vi.fn(),
  updateTransformationDefinition: vi.fn(),
  deleteTransformationDefinition: vi.fn(),
}));

const { TransformationManagement } = await import('./TransformationManagement');

const AWS_MANAGED: TransformationDefinition = {
  id: 'AWS/java-version-upgrade',
  name: 'Java Version Upgrade',
  description: 'Upgrade Java applications',
  type: 'aws-managed',
  definition_path: '',
  published: true,
  source: 'Java 8/11',
  target: 'Java 17/21',
  atx_definition_name: 'AWS/java-version-upgrade',
};

const AWS_MANAGED_UNRESOLVED: TransformationDefinition = {
  ...AWS_MANAGED,
  id: 'AWS/nodejs-version-upgrade',
  name: 'Node.js Version Upgrade',
  source: 'Node.js 14/16',
  target: 'Node.js 20',
  atx_definition_name: null,
};

// The agent's catalog also serves custom records; they belong to the other tab.
const CATALOG_CUSTOM: TransformationDefinition = {
  id: '93ae1efc-b409-4500-b007-074e79381ba8',
  name: 'agent-side-custom-transform',
  description: 'Custom definition served by the transform agent',
  type: 'custom',
  definition_path: '',
  published: false,
  atx_definition_name: 'agent-side-custom-transform',
};

const CRUD_CUSTOM: TransformationDefinition = {
  id: 'crud-1',
  name: 'my-crud-transform',
  description: 'Created through the management page',
  type: 'custom',
  definition_path: '/defs/my-crud-transform',
  published: false,
};

function openAwsManagedTab() {
  render(<TransformationManagement />);
  fireEvent.click(screen.getByRole('tab', { name: 'AWS Managed' }));
}

beforeEach(() => {
  getTransformationDefinitions.mockReset();
  getTransformations.mockReset();
  getTransformationDefinitions.mockResolvedValue([CRUD_CUSTOM]);
  getTransformations.mockResolvedValue([AWS_MANAGED, AWS_MANAGED_UNRESOLVED, CATALOG_CUSTOM]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('TransformationManagement AWS Managed tab', () => {
  it("renders the AWS-managed records from the transform agent's catalog", async () => {
    openAwsManagedTab();

    expect(await screen.findByText('Java Version Upgrade')).toBeInTheDocument();
    expect(screen.getByText('Node.js Version Upgrade')).toBeInTheDocument();
    expect(
      screen.queryByText('No AWS managed transformations available.')
    ).not.toBeInTheDocument();
  });

  it('surfaces source → target and the CLI identifier', async () => {
    openAwsManagedTab();

    expect(await screen.findByText('Java 8/11 → Java 17/21')).toBeInTheDocument();
    expect(screen.getByText('AWS/java-version-upgrade')).toBeInTheDocument();
  });

  it('marks a record with no resolved ATX identifier as not executable', async () => {
    openAwsManagedTab();

    expect(await screen.findByText(/Not executable/i)).toBeInTheDocument();
  });

  it('does not leak a non-aws-managed catalog record into the tab', async () => {
    openAwsManagedTab();

    await screen.findByText('Java Version Upgrade');
    expect(screen.queryByText('agent-side-custom-transform')).not.toBeInTheDocument();
  });

  it('keeps AWS-managed cards read-only', async () => {
    openAwsManagedTab();

    await screen.findByText('Java Version Upgrade');
    expect(
      screen.queryByRole('button', { name: /Edit Java Version Upgrade/i })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Delete Java Version Upgrade/i })
    ).not.toBeInTheDocument();
  });
});

describe('TransformationManagement Custom Transformations tab', () => {
  it('renders the CRUD records from the backend', async () => {
    render(<TransformationManagement />);

    expect(await screen.findByText('my-crud-transform')).toBeInTheDocument();
  });
});

describe('TransformationManagement independent load failures', () => {
  it('distinguishes a failed AWS-managed load from a genuinely empty catalog', async () => {
    getTransformations.mockRejectedValue(new Error('Network Error'));

    openAwsManagedTab();

    expect(
      await screen.findByText(/Could not load AWS managed transformations/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByText('No AWS managed transformations available.')
    ).not.toBeInTheDocument();
  });

  it('still renders the custom records when the AWS-managed load fails', async () => {
    getTransformations.mockRejectedValue(new Error('Network Error'));

    render(<TransformationManagement />);

    expect(await screen.findByText('my-crud-transform')).toBeInTheDocument();
  });

  it('reports a genuinely empty catalog as empty, not as a failure', async () => {
    getTransformations.mockResolvedValue([]);

    openAwsManagedTab();

    expect(
      await screen.findByText('No AWS managed transformations available.')
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Could not load AWS managed transformations/i)
    ).not.toBeInTheDocument();
  });
});
