import { test, expect } from '@playwright/test';

/**
 * Results page tab rendering (/results/:id).
 *
 * Guards against the regression where the Dep Graph, Diagrams, and Files tabs
 * rendered placeholder text instead of the real D3 / Mermaid / Recharts
 * visualisations. Assertions are positive: they require the actual chart
 * elements to exist, not merely that a placeholder is absent.
 *
 * Requires the backend (:8000) and frontend (:3000) containers to be running.
 * The spec discovers a completed analysis via the API rather than hardcoding an
 * id, so it stays valid as analyses are created and pruned.
 */

const API = 'http://localhost:8000';

/** Pick a completed analysis that has graph, diagram, and file-stats data. */
async function findUsableAnalysisId(request: {
  get: (url: string) => Promise<{ ok: () => boolean; json: () => Promise<unknown> }>;
}): Promise<string> {
  const listRes = await request.get(`${API}/api/analyses`);
  expect(listRes.ok(), 'GET /api/analyses should succeed').toBeTruthy();

  const listBody = (await listRes.json()) as
    | { analyses?: Array<{ analysis_id?: string; id?: string }> }
    | Array<{ analysis_id?: string; id?: string }>;
  const rows = Array.isArray(listBody) ? listBody : (listBody.analyses ?? []);
  const ids = rows.map((r) => r.analysis_id ?? r.id).filter((v): v is string => !!v);

  expect(ids.length, 'at least one stored analysis is required').toBeGreaterThan(0);

  // Newest first. Analysis ids are `{source}_{YYYYMMDD_HHMMSS}`, so a
  // descending lexicographic sort is also a descending chronological sort.
  const newestFirst = [...ids].sort().reverse();

  for (const id of newestFirst) {
    const graphRes = await request.get(`${API}/api/analysis/${id}/dependency-graph`);
    if (!graphRes.ok()) continue;
    const graphBody = (await graphRes.json()) as {
      dependency_graph?: { nodes?: unknown[] };
      nodes?: unknown[];
    };
    const nodes = graphBody.dependency_graph?.nodes ?? graphBody.nodes ?? [];
    if (nodes.length === 0) continue;

    const statsRes = await request.get(`${API}/api/analysis/${id}/file-stats`);
    if (!statsRes.ok()) continue;
    const statsBody = (await statsRes.json()) as { file_stats?: unknown[] };
    const stats = Array.isArray(statsBody) ? statsBody : (statsBody.file_stats ?? []);
    if (stats.length === 0) continue;

    return id;
  }

  throw new Error('no completed analysis with dependency-graph and file-stats data found');
}

async function openTab(page: import('@playwright/test').Page, label: string) {
  const tab = page.getByRole('tab', { name: label, exact: true });
  await expect(tab, `"${label}" tab should be present`).toBeVisible({ timeout: 15000 });
  await tab.click();
}

test.describe('/results/:id tab visualisations', () => {
  test.setTimeout(120000);

  let analysisId: string;

  test.beforeAll(async ({ request }) => {
    // E2E_ANALYSIS_ID pins the spec to a specific stored analysis, which is how
    // a suspected diagram regression is reproduced against a known-bad id.
    analysisId = process.env.E2E_ANALYSIS_ID || (await findUsableAnalysisId(request));
  });

  test('Dep Graph tab renders the D3 force graph, not a placeholder', async ({ page }) => {
    await page.goto(`/results/${analysisId}`);
    await openTab(page, 'Dep Graph');

    // Positive assertion: the D3 graph draws one <circle> per node.
    const nodeCircles = page.locator('svg circle');
    await expect
      .poll(async () => nodeCircles.count(), {
        message: 'D3 dependency graph should render node circles',
        timeout: 30000,
      })
      .toBeGreaterThan(0);

    // The old placeholder copy must be gone.
    await expect(page.getByText('Dependency graph visualization')).toHaveCount(0);
  });

  test('every diagram type in the toggle group renders Mermaid output', async ({ page }) => {
    await page.goto(`/results/${analysisId}`);
    await openTab(page, 'Diagrams');

    // DiagramViewer renders a ToggleButtonGroup with one button per diagram
    // type (Class / Sequence / Integration) and inlines the rendered Mermaid
    // <svg>. Asserting on only the initially selected type let the broken
    // Integration diagram ship, so iterate over all of them.
    const toggleButtons = page.locator('.MuiToggleButtonGroup-root button');
    await expect
      .poll(async () => toggleButtons.count(), {
        message: 'diagram-type toggle group should render',
        timeout: 45000,
      })
      .toBeGreaterThan(0);

    const count = await toggleButtons.count();
    const labels = await toggleButtons.allInnerTexts();
    expect(count, 'expected a button per diagram type').toBeGreaterThanOrEqual(3);

    // Scope to the render area: mermaid can leave orphaned error <svg> nodes
    // elsewhere in the document, which would mask a failed render.
    const renderArea = page.getByTestId('diagram-render-area');
    const mermaidSvg = renderArea.locator('svg[id^="mermaid"]');
    const renderFailure = renderArea.getByText(/Failed to render diagram/i);

    for (let i = 0; i < count; i += 1) {
      const label = (labels[i] ?? `#${i}`).trim();
      const button = toggleButtons.nth(i);

      if ((await button.getAttribute('aria-pressed')) !== 'true') {
        await button.click();
      }
      await expect(button, `"${label}" should become the selected type`).toHaveAttribute(
        'aria-pressed',
        'true'
      );

      // DiagramViewer publishes data-rendered-type only once the render for the
      // selected type has settled, so this cannot pass on a stale svg.
      const selected = await renderArea.getAttribute('data-diagram-type');
      await expect
        .poll(async () => renderArea.getAttribute('data-rendered-type'), {
          message: `"${label}" diagram should finish rendering`,
          timeout: 45000,
        })
        .toBe(selected);

      await expect(renderFailure, `"${label}" diagram must not fail to render`).toHaveCount(0);
      await expect(
        mermaidSvg,
        `"${label}" diagram should produce a Mermaid svg`
      ).toHaveCount(1);
      await expect(mermaidSvg).toBeVisible({ timeout: 15000 });

      // Mermaid source must be rendered, not dumped as raw text. The bug showed
      // `<pre>classDiagram class Application { +main() } …</pre>`.
      await expect(
        page.locator('pre', { hasText: /classDiagram|sequenceDiagram|graph (TD|LR)/ }),
        `"${label}" diagram must not dump raw Mermaid source`
      ).toHaveCount(0);
    }

    await expect(page.getByText('Diagram visualization')).toHaveCount(0);
  });

  test('Files tab renders a Recharts chart alongside the table', async ({ page }) => {
    await page.goto(`/results/${analysisId}`);
    await openTab(page, 'Files');

    // Recharts wraps every chart in .recharts-wrapper / .recharts-surface.
    const rechartsSurface = page.locator('.recharts-wrapper, .recharts-surface');
    await expect
      .poll(async () => rechartsSurface.count(), {
        message: 'Recharts chart should render on the Files tab',
        timeout: 30000,
      })
      .toBeGreaterThan(0);

    // The table is still expected next to the chart.
    await expect(page.getByRole('columnheader', { name: 'Extension' })).toBeVisible({
      timeout: 15000,
    });
  });
});
