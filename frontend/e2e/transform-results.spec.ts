import { test, expect } from '@playwright/test';
import { readFileSync, existsSync, unlinkSync } from 'fs';
import { execFileSync } from 'child_process';

/**
 * End-to-end proof for the transform results surface:
 *
 * 1. A completed transformation in the history sidebar reaches `/transform-results/:id`
 *    (the page was routed but unreachable — nothing in the app navigated to it).
 * 2. Changed files render with real filenames and highlighted added/removed lines
 *    (the diff payload used to carry `path`/`before`/`after`, so every tab said
 *    "unknown" and rendered zero rows).
 * 3. The download produces a valid zip of the whole transformed tree.
 *
 * Runs against the live stack; the transformation is discovered from the agent's
 * history rather than hard-coded.
 */

const AGENT = 'http://localhost:8005';

async function completedRepoId(request: import('@playwright/test').APIRequestContext) {
  const response = await request.get(`${AGENT}/transformation-history`);
  const { records } = (await response.json()) as {
    records: { repo_id: string; status: string }[];
  };
  const completed = records.filter((r) => r.status === 'completed');
  expect(completed.length, 'need at least one completed transformation in storage').toBeGreaterThan(0);

  for (const record of completed) {
    const summary = await (await request.get(`${AGENT}/diff-summary/${record.repo_id}`)).json();
    if (summary.has_changes) return record.repo_id;
  }
  throw new Error('no completed transformation with code changes');
}

/**
 * A transformation whose diff contains a *modified* file, plus that file.
 *
 * The added/removed highlighting assertions need a file with lines of both kinds, and
 * "has changes" does not imply that: a transformation that only adds files (a
 * documentation pass, for instance) has no removed lines anywhere. Searching for the
 * property the test actually depends on keeps it from breaking whenever a different
 * transformation happens to be the newest one. Status is not filtered here — the
 * subject is the results page's rendering, and a record recovered from storage carries
 * status `unknown` while still having a full diff.
 */
async function modifiedFileFixture(request: import('@playwright/test').APIRequestContext) {
  const response = await request.get(`${AGENT}/transformation-history`);
  const { records } = (await response.json()) as { records: { repo_id: string }[] };

  for (const record of records) {
    const diff = await (await request.get(`${AGENT}/diff/${record.repo_id}`)).json();
    const file = (diff.files ?? []).find((f: { status: string }) => f.status === 'modified');
    if (file) return { repoId: record.repo_id, file, diff };
  }
  throw new Error('no transformation in storage has a modified file');
}

/**
 * Reached through the in-app nav rather than `page.goto('/atx-transform')`: that URL
 * is also the nginx proxy prefix for the transform agent, so a direct request is
 * redirected to `/atx-transform/` and proxied to the API instead of the SPA. In-app
 * navigation is how a user gets there, and it is what the sidebar wiring sits behind.
 *
 * The page heading and the sidebar nav item carry the same string, "ATX Transform", so
 * both locators are scoped by role — `heading` for the page title, `button` for the nav
 * item — and pinned with `exact: true`. `getByText` and the `name` filter are substring
 * matchers, so an unscoped match on that string resolves to both elements and fails
 * strict mode; scoping resolves the ambiguity rather than hiding it behind `.first()`.
 */
async function openTransformPage(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'ATX Transform', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'ATX Transform', exact: true })
  ).toBeVisible({ timeout: 15000 });
}

test('a completed transformation in the history sidebar reaches its results page', async ({
  page,
  request,
}) => {
  const repoId = await completedRepoId(request);

  await openTransformPage(page);

  const historyEntry = page.getByRole('button', { name: `View transform results for ${repoId}` });
  await expect(historyEntry).toBeVisible({ timeout: 15000 });
  await historyEntry.click();

  await expect(page).toHaveURL(new RegExp(`/transform-results/${repoId}$`));
  await expect(page.getByRole('heading', { name: 'Transform Results' })).toBeVisible();
});

test('changed files render with real filenames and highlighted added/removed lines', async ({
  page,
  request,
}) => {
  const { repoId, file: modifiedFile, diff } = await modifiedFileFixture(request);
  const expectedNames: string[] = diff.files.map((f: { filename: string }) => f.filename);

  await page.goto(`/transform-results/${repoId}`);
  await expect(page.getByRole('heading', { name: 'Transform Results' })).toBeVisible();

  // No "unknown" tabs, and every real filename is present.
  await expect(page.getByRole('tab', { name: 'unknown' })).toHaveCount(0);
  for (const name of expectedNames) {
    await expect(page.getByRole('tab', { name })).toBeVisible();
  }

  // The summary line reports changed files, not the whole repository.
  const summary = await (await request.get(`${AGENT}/diff-summary/${repoId}`)).json();
  await expect(
    page.getByText(
      `${summary.changed_files} files changed, ${summary.additions} additions, ${summary.deletions} deletions`
    )
  ).toBeVisible();

  // Rows exist and carry the added/removed highlighting. A modified file is used so
  // both kinds of line are present to assert on.
  await page.getByRole('tab', { name: modifiedFile.filename }).click();

  const added = modifiedFile.lines.find((l: { type: string }) => l.type === 'added');
  const removed = modifiedFile.lines.find((l: { type: string }) => l.type === 'removed');
  expect(added, 'fixture file must have an added line').toBeTruthy();
  expect(removed, 'fixture file must have a removed line').toBeTruthy();

  const addedRow = page.locator('div', { hasText: added.content }).last();
  await expect(addedRow).toBeVisible();
  await expect(page.getByText('No file changes to display')).toHaveCount(0);

  // Added lines are green-tinted, removed lines red-tinted.
  const colours = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('div'));
    const seen = new Set<string>();
    for (const row of rows) {
      const bg = getComputedStyle(row).backgroundColor;
      if (bg.includes('46, 160, 67') || bg.includes('248, 81, 73')) seen.add(bg);
    }
    return Array.from(seen);
  });
  expect(colours.some((c) => c.includes('46, 160, 67')), `added highlight missing: ${colours}`).toBe(
    true
  );
  expect(
    colours.some((c) => c.includes('248, 81, 73')),
    `removed highlight missing: ${colours}`
  ).toBe(true);
});

test('the download produces a valid zip matching the transformed tree', async ({ page, request }) => {
  const repoId = await completedRepoId(request);

  await page.goto(`/transform-results/${repoId}`);
  await expect(page.getByRole('heading', { name: 'Transform Results' })).toBeVisible();

  const downloadPromise = page.waitForEvent('download', { timeout: 60000 });
  await page.getByRole('button', { name: /download code/i }).click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe(`transformed-${repoId}.zip`);

  const target = `/tmp/pw-${repoId}.zip`;
  if (existsSync(target)) unlinkSync(target);
  await download.saveAs(target);

  const bytes = readFileSync(target);
  // Local file header magic — a real zip, not an HTML error page.
  expect(bytes.subarray(0, 2).toString('binary')).toBe('PK');

  // `unzip -t` validates the archive (CRCs included) and `-Z1` lists entries. Using
  // the system tool rather than adding a zip dependency just for this assertion.
  const integrity = execFileSync('unzip', ['-t', target], { encoding: 'utf8' });
  expect(integrity).toContain('No errors detected');

  const entries = execFileSync('unzip', ['-Z1', target], { encoding: 'utf8' })
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.endsWith('/'))
    .sort();

  expect(entries.length).toBeGreaterThan(0);
  expect(entries.some((n) => n.startsWith('.git/')), 'git metadata must be excluded').toBe(false);

  // Every changed file from the diff is present in the archive.
  const diff = await (await request.get(`${AGENT}/diff/${repoId}`)).json();
  for (const file of diff.files) {
    expect(entries, `${file.filename} missing from archive`).toContain(file.filename);
  }

  unlinkSync(target);
});
