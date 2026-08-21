import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { AnalysisResult, UpgradeRecommendation } from '../types';
import { EnrichmentStatusAlert, UpgradesTab } from './AnalysisResultsDisplay';

/**
 * The reported defect: the Upgrades tab showed a row with a blank Package cell.
 *
 * The backend persists each recommendation under `name` (matching `Dependency.name`),
 * but this renderer read `package_name`, which no producer ever emits. These tests pin
 * the field the backend actually serves, plus the "we don't know the version" path that
 * previously rendered as an indistinguishable blank cell.
 */

const BASE: UpgradeRecommendation = {
  name: 'webpack',
  current_version: '5.88.2',
  recommended_version: '5.104.1',
  ecosystem: 'npm',
  reason: 'Fixes CVE-2024-43788; patched in 5.104.1',
};

describe('UpgradesTab', () => {
  it('renders the package name served by the backend', () => {
    render(<UpgradesTab data={[BASE]} />);

    expect(screen.getByText('webpack')).toBeInTheDocument();
    expect(screen.getByText('5.88.2')).toBeInTheDocument();
    expect(screen.getByText('5.104.1')).toBeInTheDocument();
  });

  it('renders the ecosystem so a row is attributable to a manifest', () => {
    render(<UpgradesTab data={[BASE]} />);

    expect(screen.getByText('npm')).toBeInTheDocument();
  });

  it('labels an undeclared current version instead of leaving a blank cell', () => {
    render(
      <UpgradesTab
        data={[
          {
            name: 'org.springframework.boot:spring-boot-starter-web',
            current_version: '',
            current_version_note: 'not declared (inherited from parent POM)',
            recommended_version: '3.x',
            ecosystem: 'maven',
            reason: 'Spring Boot 3.x supports Jakarta EE and Java 17+',
          },
        ]}
      />
    );

    expect(
      screen.getByText('not declared (inherited from parent POM)')
    ).toBeInTheDocument();
  });

  it('distinguishes "no upgrades recommended" from "no data available"', () => {
    const { unmount } = render(<UpgradesTab data={[]} />);
    expect(screen.getByText(/no upgrades recommended/i)).toBeInTheDocument();
    unmount();

    render(<UpgradesTab data={null} />);
    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument();
  });
});

/**
 * A `failed` enrichment must not read as "nothing ran".
 *
 * The backend used to record every enrichment exception as `skipped`, so a real
 * Bedrock failure was indistinguishable from a deliberate no-op. Now that the
 * backend distinguishes them, the UI has to as well — including for a status it
 * does not recognise, which must never fall through to a clean success.
 */
describe('EnrichmentStatusAlert', () => {
  it('renders nothing when enrichment completed', () => {
    const { container } = render(<EnrichmentStatusAlert status="completed" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('reports a failure as an error and names the recorded cause', () => {
    render(
      <EnrichmentStatusAlert
        status="failed"
        error='Bedrock request timed out: the model did not return a full response within the 300s read timeout'
      />
    );
    expect(screen.getByText(/AI enrichment failed/i)).toBeInTheDocument();
    expect(screen.getByText(/timed out/i)).toBeInTheDocument();
    expect(screen.queryByText(/was not run/i)).not.toBeInTheDocument();
  });

  it('distinguishes a skip from a failure', () => {
    render(<EnrichmentStatusAlert status="skipped" error="SKIP_AI_ENRICHMENT is enabled" />);
    expect(screen.getByText(/was not run/i)).toBeInTheDocument();
    expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
  });

  it('states that deterministic results are unaffected for both outcomes', () => {
    const { unmount } = render(<EnrichmentStatusAlert status="failed" />);
    expect(screen.getByText(/complete and unaffected/i)).toBeInTheDocument();
    unmount();

    render(<EnrichmentStatusAlert status="skipped" />);
    expect(screen.getByText(/complete and unaffected/i)).toBeInTheDocument();
  });

  it('flags an unrecognised status instead of rendering a clean success', () => {
    render(
      <EnrichmentStatusAlert
        status={'partial' as unknown as AnalysisResult['ai_enrichment_status']}
      />
    );
    expect(screen.getByText(/unrecognised status/i)).toBeInTheDocument();
  });
});
