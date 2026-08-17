---
inclusion: manual
---
# Acceptance Tests — Post-Generation Validation

After all tasks complete, run these manual acceptance tests to validate the app works end-to-end.
Fix any issues found before marking the build as done.

## Prerequisites

```bash
docker compose up -d --build
# Wait for all 4 services to be healthy
docker compose ps  # All should show "healthy"
```

Playwright requires Node 20+. A shell defaulting to Node 16 will fail to run the suite — check `node -v` and switch before running tests.

## Playwright Assertion Rules

When generating Playwright e2e tests from these acceptance scenarios, apply these rules:

### Rule 1: Verification must be POSITIVE not just absence-of-error
- BAD: `expect(await errorBoundary.count()).toBe(0)` — only checks no crash
- GOOD: `expect(await page.locator('svg').count()).toBeGreaterThan(0)` — verifies actual rendering

### Rule 2: Placeholder text is a test FAILURE
Any of these patterns in a rendered tab indicate the component was not wired up:
- "Task 18", "Task 19", "will be implemented"
- "placeholder", "coming soon", "under construction"
- Assert: `expect(await page.locator('text=/Task \\d+/').count()).toBe(0)`

### Rule 3: Visualization tabs require SVG or canvas elements
- Dep Graph tab: MUST contain `<svg>` with `<circle>` or `<line>` elements (D3 force graph)
- Diagrams tab: MUST contain `<svg>` elements from Mermaid rendering OR a toggle button group (DiagramViewer controls)
- Assert presence of SVG, not just absence of errors

### Rule 4: Data tabs require actual table rows
- Files, Dependencies, Upgrades: MUST have `<tr>` elements beyond the header row
- Assert: `expect(await page.locator('tbody tr').count()).toBeGreaterThan(0)`

### Rule 5: Use specific selectors over generic text checks
- For the analysis results page: each tab panel has content that can be verified with DOM structure assertions, not just "page has text"

### Rule 6: Test timeout for GitHub analysis flow
- The full GitHub clone + analysis flow takes 60-120 seconds
- Use `test.setTimeout(180000)` for any test that submits a GitHub analysis
- Use `page.waitForURL('**/results**', { timeout: 150000 })` for completion redirect

### Rule 7: Every variant of a selector must be exercised
- When a component exposes a variant selector (toggle group, tabs, dropdown), iterate EVERY option and assert on each
- Asserting only on the default selection lets broken variants ship
- BAD: `expect(await svg.count()).toBeGreaterThan(0)` after clicking one toggle button when three diagram types exist
- GOOD: loop over all toggle options, click each, assert each renders

### Rule 8: Wait for async renders to settle; never assert on stale DOM
- Async-rendering components MUST publish a settled-state marker (e.g. `data-rendered-type` alongside `data-diagram-type`)
- Tests MUST poll until the settled value matches the requested one before asserting
- Asserting immediately after triggering a render can pass against the PREVIOUS state's DOM — a false pass against known-broken data

### Rule 9: Generated content must be verified against freshly created data
- AI output and diagrams are produced and stored at creation time
- A fix to generation does not repair already-stored records
- Any assertion about generated content MUST run against a newly created analysis; re-opening a stored one proves nothing

### Rule 10: A missing test is a worse defect than a weak one
- Before trusting a suite, confirm a spec actually exercises the route in question
- A green suite with no test for a route says nothing about that route

### Rule 11: Field-name contract tests are two-directional
- Every POST endpoint the frontend calls needs BOTH assertions: the documented body returns not-422, and a body using a sibling endpoint's field name returns 422
- The negative direction is what pins the contract; with only the positive assertion a rename passes silently
- Pin the deliberate asymmetry: `POST /atx/analyze` takes `repository_url`, `POST /atx-transform/transform` takes `repo_url`, backend `POST /api/analyze/github` takes `repo_url`
- A 422 in the UI is almost always a caller/model field-name disagreement

### Rule 12: A contract test must be verified to fail against the defect
- Run the new test against the broken code, watch it fail, then fix
- A test written after the fix and never run against the defect proves nothing about whether it can detect it

### Rule 13: Endpoints returning SSE need status-only assertions
- For a streaming endpoint, assert the response status then abort the stream
- Consuming an SSE response to completion hangs the test

### Rule 14: A green healthcheck is not proof of capability
- Where a service's function is shelling out to an external binary, verify the binary inside the built image: `docker compose exec <svc> <binary> --version`
- A container can report healthy while unable to do its only job — the healthcheck only proves the web server accepted a connection

### Rule 15: Runtime resource resolution must be verified inside the container, not against the local repo
- Any asset the running service loads at runtime (prompt templates, data files, config) MUST be verified where the service actually runs: `docker compose exec <svc> …`
- A local filesystem check is not evidence. A file present in the repo can still be absent from the image (excluded by `.dockerignore`) or unreachable (resolved through a path that only exists in the repo layout) — the local check passes and the container fails
- Verify BOTH that the file exists at the in-container path AND that the service resolves it there (non-empty content read through the same resolution code path the request handler uses)
- Local-layout checks remain useful for catching a deleted file early; they are additive, never a substitute for the container check

### Rule 16: Reachability is asserted by navigating, not by visiting
- A test that calls `page.goto()` on a route proves the route renders — never that a user can arrive at it
- Assert the click path from a page the user is already on: land on the entry page, click the element that leads there, then assert the URL and the rendered content
- Every registered route needs at least one inbound navigation path from the running UI, and that path is asserted, not assumed
- This is why a finished transformation's results surface shipped unreachable and green: `/transform-results/:id` was routed, rendered correctly for anyone typing the URL, and nothing repo-wide navigated to it

### Rule 17: Durability is asserted across a restart, not across two reads
- A second read in the same process is served by the same in-memory structure that will not survive
- Restart the service, wait for health, then assert BOTH the listing and every action on its entries
- The symptom this catches is a history list that survives the restart while every action on it 404s against data still sitting on disk
- Applies to any state a route gates on: listing the row is not the assertion, acting on it is

### Rule 18: Assert the source a view read, not only what it rendered
- A view can render entirely plausible content from the wrong source
- A filter over a collection that never holds the records it selects is empty with nothing thrown — the predicate is correct and the collection is wrong
- Assert the ENDPOINT the view read, not only the shape of what appeared: intercept the request, or assert the rendered entries correspond to that endpoint's payload
- "Both tabs render" was true throughout the defect. The AWS Managed tab rendered, sourced from the backend CRUD collection, and was empty for every user

### Rule 19: A count that can legitimately be zero is asserted as present, not as non-zero
- `expect(count).toBeGreaterThan(0)` is vacuous on exactly the value that matters — the zero is the case the reader cannot otherwise infer
- An absent key and a `0` are different facts; assert the key EXISTS (`has("source_files_changed")`), then assert its value
- Assert the parts reconcile with the total: per-category counts sum to `changed_files`, per-category additions and deletions sum to the uncapped totals
- A documentation-only run reporting only a total is indistinguishable from a run whose source changes went missing

### Rule 20: An internal link is asserted by following it
- Rendering a link proves nothing about its destination. Click it and assert where it landed — which document is selected, which heading is in view, whether a new tab opened
- Assert that a link with no destination is NOT rendered as one: a non-navigating element naming the target, never an anchor that opens a dead tab and never one that silently does nothing
- This is Rule 16 one level in — from routes to links

### Rule 21: A status is asserted by value, and every value in the union gets a scenario
- Asserting only the success value, or only the values a happy path produces, leaves the rest free to mean anything
- A suite that asserts `completed` and `skipped` passed through five consecutive `failed` runs because the code reported them as `skipped` and nothing distinguished the two
- Each documented status needs a scenario that reaches it deliberately, **by the lever that actually causes it**: force a timeout to test `failed`, set the disable flag to test `skipped`
- Using one cause to test another value conflates them permanently

### Rule 22: A test that asserts an error must cite the requirement making that error correct
- An assertion that an operation fails is a claim about intended behaviour. Without a requirement behind it, the test is not verifying a behaviour — it is **pinning a defect**
- Name the requirement or design section in the scenario. Where the design obliges the operation to **succeed**, the TEST is the defect: fix the test first, then the product
- **Two different axes, which compose rather than conflict.** Rule 10 ranks **missing above weak** on the coverage axis: a route with no spec is a worse defect than a route with a loose assertion. This Rule ranks **wrong above missing** on the correctness axis — that ordering is Build Constraint 78's, not Rule 10's, and citing Rule 10 for it inverts what Rule 10 says. Read together the order is: wrong, then missing, then weak. Wrong tops it because absence of coverage invites a look while a green assertion closes the question, so the contradiction survives *because* something is passing
- Test 15 scenario 4 asserted the ATX CLI's missing-`additionalPlanContext` startup error, plus a `failed` status, as the expected outcome for `java-version-upgrade` — while design.md required a default `-g` configuration for exactly that definition and nothing implemented one. The suite was green on the behaviour the design forbade, and every run produced fresh evidence the defect was intended. See Build Constraint 78

### Rule 23: A locator is role-scoped and matched exactly wherever its string is not unique
- **Playwright's `getByText()` and the `name` filter of `getByRole()` are substring matchers by default.** A locator written against a string that occurs inside a longer string either resolves onto the wrong node or matches several and fails strict mode. Neither outcome is a product defect — in both the test is wrong about what it is looking at, and a rebuild reproduces it because the colliding strings are each independently mandated
- Every locator matching a string that is not unique in the rendered page MUST be **scoped by role** AND pass **`exact: true`** — `page.getByRole('button', { name: 'Cancel', exact: true })`, never `page.getByText('Cancel')`
- **`.first()`, `.nth(n)`, `.last()` and any ordinal index are forbidden as disambiguation.** They pin the ambiguity instead of resolving it: the locator still matches several nodes, and the test now depends on DOM order, so a reordered sidebar silently retargets the assertion onto a different element while staying green. Where a role-scoped exact name still matches more than one node, disambiguate by **container** (`page.getByRole('navigation').getByRole('button', { name: …, exact: true })`) or by an explicit test id — never by position
- **A conditional visibility guard is not an acceptable way to absorb a wrong locator.** `if (await locator.isVisible()) { await locator.click(); expect(…) }` makes the assertion unreachable when the locator is wrong, so the spec passes on exactly the failure it was written to catch. That is Rule 1's defect reached from the locator side, and it is why three wrong nav labels shipped green in `navigation.spec.ts`. A locator that may legitimately resolve to nothing is not an assertion target — assert its count instead, and let a wrong locator fail
- The three collisions this Rule exists for, all real and all designed in:
  - `getByRole('button', { name: 'Cancel' })` on the ATX Analysis page matches BOTH the Cancel button and a sidebar conversation row: the row is a `ListItemButton` (role `button`) whose status Chip sits inside it, so a row in `cancelled` status contributes `Cancel` as a substring of its accessible name. Strict mode fails. The status-bearing sidebar, the `cancelled` value, and the Cancel button are each mandated separately (Test 13 scenarios 3 and 4), so the collision is permanent — `exact: true` is what separates them
  - `ATX Transform` names both a nav item under AWS Transform and the heading of the page that item opens, so an unscoped match resolves two nodes on the destination page
  - The brand string `Code Analyse & Transform` contains the nav section name `Code Analyse`, which is itself also the name of the routed child item beneath that section — three nodes for one string (design.md "Site Title (brand string)")
- This is Build Constraint 79's locator clause stated as a test rule, so it binds **every** spec rather than only the specs touching a renamed display string

---

## Test 1: Frontend Loads & Navigation Works

1. Open http://localhost:3000
2. Verify Dashboard page renders (quick action cards, stats, recent analyses table)
3. Click each nav item in the left sidebar — verify page changes (no "under construction" stubs). **These are the labels the sidebar actually renders** (design.md "Shared UI Components" → `Navigation.tsx`, three sections). A label invented here becomes a locator that matches nothing:
   - **Main** — `Dashboard` → `/`
   - **Code Analyse** (section header, a collapse toggle — it navigates nowhere)
     - `Code Analyse` → `/analysis`
     - `Previous Analyses` → `/previous`
   - **AWS Transform** (section header, a collapse toggle — it navigates nowhere)
     - `Transforms` → `/transformations`
     - `ATX Analyse` → `/atx-analysis`
     - `ATX Transform` → `/atx-transform`

   Not `Code Analysis`, not `ATX Analysis`, not `Transformations` — those three strings appear nowhere in the DOM.
4. Verify the sidebar highlights the active item

**Locator note (Rule 23).** Three strings here are ambiguous and each MUST be role-scoped with `exact: true`:
- `Code Analyse` resolves **three** nodes — the brand heading `Code Analyse & Transform`, the section toggle, and the routed child item. The role scope drops the brand (a heading, not a button); the remaining two share an accessible name exactly and are separated by container, never by `.nth()`
- `ATX Transform` resolves the nav item and, once navigated, the destination page heading — `AtxJavaTransformPage.tsx` renders the same string as its `h6`
- `Transform` as a match string resolves four nodes in the sidebar alone — the brand `Code Analyse & Transform`, the `AWS Transform` section toggle, `Transforms`, and `ATX Transform`. Any locator naming a bare `Transform` fragment is a Rule 23 violation; name the full label and pass `exact: true`

Assert each click unconditionally: land on `/`, expand the owning section, click the item, assert `page.toHaveURL()` and one positive content assertion on the destination. Wrapping any of these in `if (await …isVisible())` fails this Test under Rule 23 — the guard makes a wrong label indistinguishable from a working one, which is how the three wrong labels above survived.

**Pass criteria:** All six routes render real content, each reached by an unconditional click on the label listed above. Active-item highlight tracks the current route. No console errors in browser dev tools (except expected 401/404 from backend when no data exists).

---

## Test 2: Backend Health & Core API

```bash
# Health endpoint
curl -s http://localhost:8000/health | jq .
# Expected: {"status": "healthy"}

# Auth config
curl -s http://localhost:8000/api/auth/config | jq .
# Expected: {"mode": "disabled"} (since AUTH_DISABLED=true)

# List analyses (empty at first)
curl -s http://localhost:8000/api/analyses | jq .
# Expected: {"analyses": []} — an ENVELOPE, not a bare [].
# backend/routes/analysis.py::list_analyses returns {"analyses": [...]}, and Build
# Constraint 7 lists `/analyses`→`.analyses` among the keys api.ts must unwrap.
# A bare [] expectation fails against correct code; assert on `.analyses`, e.g.
#   curl -s http://localhost:8000/api/analyses | jq -e 'has("analyses") and (.analyses|type=="array")'

# 404 for nonexistent analysis
curl -s http://localhost:8000/api/analysis/nonexistent/status
# Expected: {"detail": "..."} with 404 status
```

**Pass criteria:** All responses match expected format. No 500 errors.

---

## Test 3: ZIP Upload Analysis Flow (Frontend → Backend)

1. Go to http://localhost:3000/analysis
2. Switch to the "ZIP Upload" tab
3. Create a tiny test ZIP:
   ```bash
   mkdir -p /tmp/test-code && echo 'public class Hello { public static void main(String[] args) {} }' > /tmp/test-code/Hello.java && cd /tmp && zip -r test-code.zip test-code/
   ```
4. Drag-and-drop `/tmp/test-code.zip` into the upload area
5. Click "Start Analysis"
6. Verify:
   - Progress tracker shows and advances through steps
   - On completion, results display appears with tabs
   - File Stats tab shows `.java` file counted
   - Folder Structure tab shows the file tree
7. Go to Previous Analyses — verify the analysis appears in the table
8. Click "View" — verify it navigates to results

**Pass criteria:** Full upload → parse → display flow works without errors.

---

## Test 4: GitHub Clone Analysis Flow

1. Go to http://localhost:3000/analysis
2. In the GitHub tab, enter a public repo URL: `https://github.com/spring-projects/spring-petclinic`
3. Set branch to `main`
4. Click "Start Analysis"
5. Verify progress tracker advances and results display on completion

**Pass criteria:** Clone + analysis completes. Results show Java files, dependencies (pom.xml parsed), and diagrams.

---

## Test 5: ATX Analysis Agent

```bash
# Health check
curl -s http://localhost:8004/health | jq .
# Expected: {"status": "healthy"}

# List conversations (empty at first)
curl -s http://localhost:8004/conversations | jq .
# Expected: {"conversations": []} — the envelope is fixed by Build Constraint 33.
# NOT a bare []; "either shape is fine" is not a contract and passes vacuously.

# Analysis definitions
curl -s http://localhost:8004/analysis-definitions | jq .
# Expected: {"definitions": [...]} — the available analysis types under `.definitions`

# ATX CLI is actually installed in the image (Rule 14)
docker compose exec atx-analysis-agent atx --version
# Expected: a version string, exit 0. "not found" = broken image, regardless of healthcheck

# Contract — CORRECT field name (repository_url). /analyze responds with SSE (Rule 13),
# so cap the read with head instead of letting curl run to completion.
curl -s -N -o /dev/null -w "%{http_code}" -X POST http://localhost:8004/analyze \
  -H "Content-Type: application/json" \
  -d '{"repository_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main", "analysis_type": "code-assessment"}' | head -1
# Expected: 200 (NOT 422)

# Contract — WRONG field name (repo_url, the ATX Transform field)
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8004/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main"}'
# Expected: 422 (field name mismatch)
```

Frontend test:
1. Go to http://localhost:3000/atx-analysis
2. Verify the page loads (conversation list, new analysis form)
3. Enter a repo URL and click Start — verify SSE stream appears in terminal box
4. Page-driven contract check: fill the repository URL field, click Start, and verify the `/atx/analyze` response status is NOT 422

**Pass criteria:** Agent responds to health checks and serves definitions. `atx --version` succeeds inside the container. `repository_url` returns not-422 and `repo_url` returns 422. Frontend renders the ATX analysis UI and its submit does not 422.

---

## Test 6: ATX Transform Agent

```bash
# Health check
curl -s http://localhost:8005/health | jq .
# Expected: {"status": "healthy"}

# List transformations
curl -s http://localhost:8005/transformations | jq .
# Expected: {"definitions": [...]} — the transformation definitions under `.definitions`

# ATX CLI is actually installed in the image (Rule 14)
docker compose exec atx-transform-agent atx --version
# Expected: a version string, exit 0. "not found" = broken image, regardless of healthcheck

# Contract — CORRECT field name (repo_url)
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8005/transform \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main", "transformation_type": "AWS/java-version-upgrade"}'
# Expected: 200 (NOT 422)

# Contract — WRONG field name (repository_url, the ATX Analysis field)
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8005/transform \
  -H "Content-Type: application/json" \
  -d '{"repository_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main", "transformation_type": "AWS/java-version-upgrade"}'
# Expected: 422 (field name mismatch)
```

Frontend test:
1. Go to http://localhost:3000/atx-transform
2. Verify the page loads (repo input, transformation type selector)
3. Go to http://localhost:3000/transformations

**Transformation Management scenarios:**

4. **AWS Managed tab is populated, and from the catalog that owns the records**
   - Verify the AWS Managed tab renders one read-only card per AWS-managed catalog entry — at least 13
   - Verify each card shows `source → target` and the resolved `atx_definition_name`
   - **Assert which endpoint the tab read.** The AWS Managed content MUST come from `GET /atx-transform/transformations` — assert the page issues that request and that the rendered cards correspond to its AWS-managed entries. A tab sourced from `GET /api/transformations/definitions` is the defect: no AWS-managed record is ever written to that collection, so the `type === 'aws-managed'` filter is a correct predicate over a collection that never holds what it selects. The tab is empty by construction and nothing throws.
   - Verify no AWS-managed card offers an edit or a delete action — the catalog is read-only
   - Verify a custom entry present in the agent catalog does NOT appear in the AWS Managed tab; the Custom tab already owns those records
   - Verify an entry with `atx_definition_name: null` is marked not executable, and offers no action that would submit it

5. **The two loads are independent**
   - Stop the agent: `docker compose stop atx-transform-agent`
   - Verify the AWS Managed tab reports a **load failure naming the source**, while the Custom tab still renders its records
   - Verify the failure text DIFFERS from the text shown when the catalog genuinely carries no AWS-managed entries
   - Restart afterwards: `docker compose start atx-transform-agent`
   - A single shared `try`/`catch` set both lists to `[]`, so an unreachable agent was indistinguishable from "none available"

"Both tabs render" passed for the entire life of the defect. The tabs rendered, and one of them was permanently empty.

**Pass criteria:** Agent responds to health checks. `atx --version` succeeds inside the container. `repo_url` returns not-422 and `repository_url` returns 422. Transform page renders correctly. The AWS Managed tab renders read-only cards sourced from the transform agent's catalog, marks unexecutable entries as such, and reports a named load failure when the agent is down while the Custom tab stays populated.

---

## Test 7: Transformation Definitions CRUD

```bash
# Create a custom transformation
curl -s -X POST http://localhost:8000/api/transformations/definitions \
  -H "Content-Type: application/json" \
  -d '{"name": "test-transform", "description": "Test", "definition_content": "# Test"}' | jq .

# List — should include the new one
curl -s http://localhost:8000/api/transformations/definitions | jq .

# Delete it
curl -s -X DELETE http://localhost:8000/api/transformations/definitions/test-transform | jq .
```

**Pass criteria:** CRUD operations succeed with proper responses.

---

## Test 8: Frontend Error Handling

1. Stop the backend: `docker compose stop backend`
2. Refresh http://localhost:3000 — verify no crash, shows graceful error
3. Restart: `docker compose start backend`
4. Refresh — verify recovers normally

**Pass criteria:** Frontend doesn't crash when backend is down. Shows meaningful error state.

---

## Test 9: GitHub Analysis API Contract (Frontend → Backend Field Names)

This test validates the frontend sends the correct field names that match the backend Pydantic model. `POST /api/analyze/github` is served by `backend/models.py::GithubAnalysisRequest`, whose fields are `repo_url` (required), `branch`, `pat_token`.

```bash
# Direct API test — should return analysis_id (not 422)
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:3000/api/analyze/github \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main"}'
# Expected: 200 (NOT 422)

# WRONG field name — should return 422
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:3000/api/analyze/github \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main"}'
# Expected: 422 (field name mismatch)
```

Frontend test:
1. Go to http://localhost:3000/analysis
2. In the GitHub tab, enter `https://github.com/Deenadayaalan/task-manager`
3. Set branch to `main`
4. Click "Start Analysis"
5. Verify: No 422 error in browser console, progress tracker appears and advances

**Pass criteria:** Frontend sends `repo_url` (not `github_url`) matching the backend `GithubAnalysisRequest` model in `backend/models.py`. Analysis starts successfully.

**Playwright e2e validation** (in `frontend/e2e/routes.spec.ts`):
```typescript
test('GitHub analysis form submits without 422', async ({ page }) => {
  await page.goto('/analysis');
  // Fill GitHub URL input — request body field is repo_url
  const urlInput = page.locator('input[placeholder*="github"]').first();
  await urlInput.fill('https://github.com/Deenadayaalan/task-manager');
  // Submit and verify no 422 in network responses
  const [response] = await Promise.all([
    page.waitForResponse(resp => resp.url().includes('/analyze/github')),
    page.locator('button:has-text("Analyze"), button:has-text("Start")').first().click(),
  ]);
  expect(response.status()).not.toBe(422);
});
```

---

## Test 10: Full GitHub Analysis E2E with Results Tabs

Submits a real GitHub repository analysis via the UI and validates all 8 result tabs render with content. This is the most comprehensive end-to-end validation.

**Test repository:** `https://github.com/Deenadayaalan/task-manager` (branch: `main`)

**Scenarios:**

1. **Submit GitHub analysis from the UI**
   - Navigate to the analysis page
   - Enter the test repository URL and branch
   - Click the submit/analyze button
   - Verify no 422 or network errors occur

2. **Analysis completes successfully**
   - Wait for the progress tracker to reach 100%
   - Verify the results view appears (tab interface visible)
   - Verify all 8 tabs are present: Summary, Files, Folders, Dependencies, Dep Graph, Upgrades, Diagrams, Documentation

3. **Each tab renders without JavaScript errors**
   - Click through every tab sequentially
   - Verify no `TypeError`, `ReferenceError`, or uncaught exceptions in the browser console
   - Verify no error boundary or "Something went wrong" message appears on any tab

4. **Summary tab shows meaningful content**
   - Verify the tab content area has key-value pairs or stat cards
   - Verify at least one numeric value is visible (file count, line count)
   - Verify content is NOT just "No summary available"

   **Playwright assertion:**
   ```typescript
   const summaryTab = page.locator('[role="tab"]:has-text("Summary")');
   await summaryTab.click();
   await page.waitForTimeout(1000);

   // Must have table rows or visible text content (not "No summary available")
   const noData = page.locator('text=/No summary available/');
   expect(await noData.count()).toBe(0);

   // Must show actual data — verify table has rows
   const tableRows = page.locator('table tbody tr');
   expect(await tableRows.count()).toBeGreaterThan(0);
   ```

5. **Files tab shows detected file types**
   - Verify a table is rendered with file data
   - Verify at least one row has content (the test repo has .js, .json, .md files)

   **Playwright assertion:**
   ```typescript
   const filesTab = page.locator('[role="tab"]:has-text("Files")');
   await filesTab.click();
   await page.waitForTimeout(1000);

   // Must have table rows with file data
   const tableRows = page.locator('table tbody tr');
   expect(await tableRows.count()).toBeGreaterThan(0);

   // Verify it's not showing "No file statistics available"
   const noData = page.locator('text=/No file statistics available|No data/');
   expect(await noData.count()).toBe(0);
   ```

6. **Dependencies tab shows packages**
   - Verify a dependencies table is rendered
   - Verify npm packages from the test repo's package.json appear

   **Playwright assertion:**
   ```typescript
   const depsTab = page.locator('[role="tab"]:has-text("Dependencies")');
   await depsTab.click();
   await page.waitForTimeout(1000);

   // Must have table rows
   const tableRows = page.locator('table tbody tr');
   expect(await tableRows.count()).toBeGreaterThan(0);

   // Verify it's not showing "No dependencies" message
   const noData = page.locator('text=/No dependencies available|No dependencies found/');
   expect(await noData.count()).toBe(0);
   ```

7. **Dep Graph tab shows interactive D3.js SVG visualization**
   - Click the "Dep Graph" tab
   - Wait 2 seconds for D3 force simulation to render
   - Verify: an `<svg>` element exists inside the tab content area
   - Verify: the SVG contains `<circle>` elements (graph nodes)
   - Verify: NO placeholder text like "DependencyGraph component" or "Task 18" is visible
   - Verify: the container has visible height (> 100px)

   **Playwright assertion:**
   ```typescript
   // Click Dep Graph tab
   const depGraphTab = page.locator('[role="tab"]:has-text("Dep Graph")');
   await depGraphTab.click();
   await page.waitForTimeout(2000); // D3 force simulation needs time

   // MUST have SVG element (D3 renders to SVG)
   const svg = page.locator('[role="tabpanel"] svg, main svg').first();
   await expect(svg).toBeVisible({ timeout: 5000 });

   // MUST have circle elements (graph nodes)
   const circles = page.locator('svg circle');
   expect(await circles.count()).toBeGreaterThan(0);

   // MUST NOT have placeholder text
   const placeholder = page.locator('text=/DependencyGraph component|Task 18|force-directed/');
   expect(await placeholder.count()).toBe(0);
   ```

8. **Diagrams tab shows rendered Mermaid diagrams (not raw source)**
   - Click the "Diagrams" tab
   - Wait 3 seconds for Mermaid dynamic import + render
   - Verify: either rendered SVG diagrams are visible OR the DiagramViewer toggle buttons are present
   - Verify: NO `<pre>` blocks containing raw mermaid syntax (classDiagram, sequenceDiagram, graph) are the ONLY content
   - Verify: NO placeholder text like "DiagramViewer — Task 18" or "Mermaid rendering" is visible

   **Playwright assertion:**
   ```typescript
   // Click Diagrams tab
   const diagramsTab = page.locator('[role="tab"]:has-text("Diagrams")');
   await diagramsTab.click();
   await page.waitForTimeout(3000); // Mermaid dynamic import + render

   // Verify DiagramViewer is rendered (has toggle buttons or SVG output)
   const toggleGroup = page.locator('[role="group"] button, .MuiToggleButtonGroup-root button');
   const svgDiagram = page.locator('[role="tabpanel"] svg, main svg');
   const hasToggle = await toggleGroup.count() > 0;
   const hasSvg = await svgDiagram.count() > 0;
   // DiagramViewer shows EITHER toggle buttons (for switching diagram types) OR rendered SVG
   expect(hasToggle || hasSvg).toBeTruthy();

   // MUST NOT show only raw mermaid text in pre blocks as the sole content
   const prePlaceholder = page.locator('text=/DiagramViewer.*Task 18|Mermaid rendering/');
   expect(await prePlaceholder.count()).toBe(0);
   ```

9. **Documentation tab shows AI-generated content**
   - Verify rendered markdown prose is visible (headings, paragraphs)
   - Verify it does NOT show the same Mermaid diagrams as the Diagrams tab

10. **Cross-tab regression: no placeholder text on ANY tab**
    - Click through ALL 8 tabs sequentially
    - On each tab, verify NO text matching `/Task \d+|placeholder|will be implemented|coming soon/` is visible
    - This catches future regressions where a real component gets reverted to placeholder

    **Playwright assertion:**
    ```typescript
    const allTabs = ['Summary', 'Files', 'Folders', 'Dependencies', 'Dep Graph', 'Upgrades', 'Diagrams', 'Documentation'];
    for (const tabName of allTabs) {
      const tab = page.locator(`[role="tab"]:has-text("${tabName}")`);
      await tab.click();
      await page.waitForTimeout(1500);

      // No placeholder text on any tab
      const placeholders = page.locator('text=/Task \\d+|placeholder|will be implemented|coming soon/i');
      const count = await placeholders.count();
      if (count > 0) {
        const text = await placeholders.first().textContent();
        throw new Error(`Tab "${tabName}" contains placeholder text: "${text}"`);
      }
    }
    ```

**Pass criteria:** Analysis completes successfully. All 8 tabs render without JavaScript errors. Tabs with data (Summary, Files, Folders, Dependencies) show real parsed content from the test repository. Visualization tabs (Dep Graph, Diagrams) show actual graphics, not text. Documentation tab shows AI-generated prose.

---

## Test 10a: Results Page — Full Tab and Variant Coverage

The results route MUST have its own spec. Run it against a **freshly created** analysis (Rule 9), never a stored one.

**Required assertions:**

1. All 8 tabs are visited: Summary, Files, Folders, Dependencies, Dep Graph, Upgrades, Diagrams, Documentation
2. Dep Graph: `svg circle` count > 0
3. Files, Dependencies, Upgrades: `tbody tr` count > 0 on each
4. Diagrams: iterate EVERY diagram-type toggle option (Rule 7); for each, poll the settled-state marker (Rule 8) then assert a Mermaid `<svg>` exists and no render-failure text is present
5. No placeholder text on ANY tab (`/Task \d+|placeholder|will be implemented|coming soon/i` count = 0)
6. Documentation: FAIL on contextless output — assert no `/No codebase provided for analysis/` and no empty/fallback body while status is `completed`
7. Upgrades tab — the row states a package, a manifest and an actionable recommendation:
   - The Package cell is non-empty for EVERY row and equals that record's `name`. The renderer read `package_name`, which the backend has never produced
   - An Ecosystem column exists and is populated from `ecosystem` — produced all along and absent from the interface, so no row was attributable to a manifest
   - A row whose current version is undeterminable renders its explanatory note, never a blank cell
   - A freshly created analysis of the test repo yields at least one advisory-grounded recommendation naming a CVE and a fixed version. Reference case: `webpack@5.88.2` → `5.104.1` citing three CVEs
   - An EOL package carrying advisories with no published fix yields **no** row — `angular@1.8.3`, 10 advisories, no `fixed` event
   - "No upgrades recommended" is a different string from the load-failure state
   - A blank Package cell and a legitimately unknown value looked identical, which is why the defect went unreported. Asserting only that the table has rows passes against a row of blanks.

**Pass criteria:** Every tab and every diagram variant renders real content on a newly created analysis. Every Upgrades row names its package and its ecosystem, and every recommendation is one the reader can act on.

---

## Test 11: AI Enrichment — Documentation Generation

Validates that the analysis pipeline's Phase 2 (AI enrichment) generates meaningful documentation and summary using Bedrock Claude.

**Prerequisites:** Valid AWS credentials with `bedrock:InvokeModel` permission in `.env` file.

**Test repository:** `https://github.com/Deenadayaalan/task-manager` (branch: `main`)

**Scenarios:**

1. **AI documentation is generated during analysis**
   - Submit a **fresh** GitHub analysis via the UI (Rule 9 — a stored analysis keeps its generation-time text and proves nothing about a generation fix)
   - Wait for analysis to complete (status 100%)
   - Verify `ai_enrichment_status` **equals** `"completed"` in the summary response. Any other value (`"skipped"`, `"failed"`, absent) is a FAILURE for this scenario — the `skipped` path is covered separately by scenario 4, under an explicit `SKIP_AI_ENRICHMENT` setup and nothing else, and the `failed` path by scenario 7
   - Verify the documentation endpoint returns non-empty content
   - Verify the generated documentation **names real technologies detected in the analysed repository** (for the test repo: Node.js/Express/React and packages from its `package.json`). Generic prose that names no technology from the repo is a FAILURE, even when the status is `completed`
   - Verify the documentation does NOT contain contextless output such as `/No codebase provided for analysis/` — `completed` with no substituted context is a FAILURE, not a pass

2. **Documentation tab shows AI-generated content (not Mermaid diagrams)**
   - After analysis completes, click the Documentation tab
   - Verify it shows rendered markdown prose (headings, paragraphs, lists)
   - Verify it does NOT show raw Mermaid diagram syntax (no `classDiagram`, `sequenceDiagram` keywords as raw text)
   - Verify it contains project-relevant content (mentions actual dependencies, file types, or frameworks detected)

3. **Summary tab shows AI-generated executive summary**
   - After analysis completes, click the Summary tab
   - Verify an AI-generated markdown section appears above the stats cards
   - Verify it contains architectural insights or recommendations (not just raw JSON)
   - Verify the stats cards (Total Files, Total Lines, Languages, Dependencies) still render below

4. **Deliberate skip — reached by the setting, never by breaking Bedrock**
   - Set `SKIP_AI_ENRICHMENT=true` in environment. Do NOT use expired credentials, a bad region, or an unreachable endpoint to reach this scenario: `skipped` means enrichment was **not attempted**, and using a Bedrock fault to test it is the conflation that hid the defect for five consecutive analyses
   - Submit a new analysis
   - Verify analysis still completes successfully (status 100%)
   - Verify `ai_enrichment_status` **equals** `"skipped"`
   - Verify the recorded `ai_enrichment_error` **names the setting** — a reader must be able to tell a deliberate skip from a fault without reading the container's environment
   - Verify Documentation tab shows a fallback message naming Bedrock unavailability (not a crash)
   - Verify Summary tab still shows stats cards, with the skip reported as informational rather than as an error

5. **Documentation content quality**
   - Run against a **freshly created** analysis with `ai_enrichment_status == "completed"` (Rule 9)
   - Verify the documentation includes:
     - A "Project Overview" or equivalent section
     - **Real technologies detected in the analysed repo, named explicitly** (e.g., Node.js, Express, React) — cross-check the names against the dependencies/file-stats the same analysis reported, so the assertion fails if the model emitted plausible-but-unrelated technologies
     - A dependencies or libraries section
     - More than 500 characters of meaningful content (not boilerplate)
   - Verify absence of contextless fallback text (`/No codebase provided for analysis/`, empty body) while status is `completed`
   - Reference measurement from two fresh analyses reporting `completed`: 2,795 / 16,141 and 2,862 / 16,190 characters of summary / documentation, the documentation naming Java Spring Boot, AngularJS, Terraform, Maven, Docker, webpack and TypeScript and counting 49 files / 162 KB. A `completed` run whose documentation is an order of magnitude shorter than this, or names none of the repo's technologies, is the contextless case wearing a success status

6. **Prompt files are loaded correctly**
   - Local layout (fast fail if a file was deleted — additive only, never sufficient on its own per Rule 15):
     - Verify `backend/prompts/documentation-generation.md` exists and is non-empty
     - Verify `backend/prompts/analysis-summary.md` exists and is non-empty
   - **In-container resolution (Rule 15) — this is the binding check:**
     ```bash
     # The templates are actually present in the built image
     docker compose exec backend ls -l /app/prompts/documentation-generation.md /app/prompts/analysis-summary.md
     # Expected: both listed, size > 0. "No such file" = shipped image is broken
     #   even though the repo has the file (check backend/.dockerignore)

     docker compose exec backend sh -c 'wc -c < /app/prompts/documentation-generation.md'
     # Expected: > 0

     # The service resolves them through its OWN resolution code path, not just by path existence.
     # agents.prompt_loader.load_prompt_result reports used_fallback / source / tried_paths.
     docker compose exec backend python -c "from agents.prompt_loader import load_prompt_result as l; r = l('documentation-generation'); print(r.source, r.used_fallback, r.tried_paths); assert not r.used_fallback, 'fell back to built-in default inside the container'"
     docker compose exec backend python -c "from agents.prompt_loader import load_prompt_result as l; r = l('analysis-summary'); assert not r.used_fallback, 'fell back to built-in default inside the container'"
     # Expected: exit 0, source is an /app/prompts/... path, used_fallback is False.
     # used_fallback True = the template did not resolve in the container even though the repo has it.
     # This is the exact failure that yields "No codebase provided for analysis" with status completed.
     ```
   - Verify the backend loads prompts from these files (not hardcoded defaults) — a `completed` documentation response that contains no substituted repo context indicates the fallback path was taken
   - Test: rename a prompt file temporarily → verify the default fallback prompt is used instead (graceful degradation), and restore it afterwards

7. **An attempted call that raised is `failed`, not `skipped`**
   - Force a real Bedrock failure with the honest lever: `BEDROCK_READ_TIMEOUT_SECONDS=1`, so the call is attempted and raises. Do NOT reach this scenario through `SKIP_AI_ENRICHMENT` — that is scenario 4, and the two must be reached by different causes
   - Submit a fresh analysis
   - Verify `ai_enrichment_status` **equals** `"failed"`, **not** `"skipped"`. `skipped` here is the shipped defect: the handler was `except Exception: ai_enrichment_status = "skipped"`, so a Bedrock read timeout was indistinguishable from a deliberate disable, and five consecutive failed analyses read as "the AI step didn't run". Asserting only that the analysis completed passes against that defect
   - Verify `ai_enrichment_error` is non-empty and **names the cause and the operator action** — for a read timeout, the configured timeout value and the call that exceeded it. A generic "enrichment failed" is a FAILURE of this scenario: a timeout, a denied model, an absent credential, a throttle and a wrong region are five different things to do
   - Verify the analysis still reaches 100% and every deterministic result persists — `file_stats`, `folder_structure`, `dependencies`, `dependency_graph`, `upgrade_recommendations`, `diagrams` all present and readable
   - Verify the **Summary** tab surfaces it as an error carrying the recorded `ai_enrichment_error`, and states that the deterministic results are complete and unaffected
   - Verify the **Documentation** tab surfaces it as an error too, and does NOT fall through to "No AI documentation available yet". An attempted call that errored is a different fact from an analysis that has no documentation
   - Restore the timeout setting afterwards

8. **A succeeded stage's output survives a later stage's failure**
   - Arrange for one enrichment stage to succeed and a later one to fail — `analysis-summary` completes at 14.7s while `documentation-generation` is cut off (a read timeout between the two measured latencies does this)
   - Verify the succeeded stage's output is **stored**: `ai_summary` present in the summary response
   - Verify it is **rendered**: the Summary tab shows the AI narrative above the stats cards
   - Verify `ai_enrichment_error` names **which** stage failed (`documentation-generation`), not merely that enrichment failed
   - Verify the Documentation tab reports the missing document as **failed**, never as absent
   - `result["ai_summary"]` was assigned only after both calls returned, so all five failed runs carry **absent** AI fields where a completed summary should have been. Asserting that a failed run has no AI fields passes against that defect

9. **Timeout and retry configuration, verified in the running container (Rule 15)**
   - The 60s default cannot serve this workload. Measured against a real analysis context:

     | Call | Prompt | Output | Wall clock | Against the 60s default |
     |------|--------|--------|-----------|-------------------------|
     | `analysis-summary` | 10,397 chars | 654 tokens | **14.7s** | passes |
     | `documentation-generation` | 12,746 chars | 4,034 tokens | **75.1s** | **cannot succeed** |

   - Verify the bedrock-runtime client's `read_timeout` is **explicitly set** and **greater than botocore's 60s default**
   - Verify the SDK's own retries are **disabled** beneath the explicit policy — that is **`total_max_attempts: 1`** on the client config. Nested retry layers multiply: botocore's defaults re-ran the same under-timed 75.1s request five times, so enrichment burned **5.5 minutes** on five identical timeouts before reporting anything
   - **`total_max_attempts` and `max_attempts` are different settings and one word apart.** `total_max_attempts` counts **every** request including the first, so `1` means one attempt and no retry — this is the correct key. `max_attempts` counts **retries on top of** the first request, so `max_attempts: 1` leaves one SDK retry enabled: exactly the nested-retry multiplier this section exists to remove, doubling the worst case to roughly 20 minutes. botocore itself documents `total_max_attempts` as preferred and, at client construction, **pops `max_attempts` and rewrites it as `total_max_attempts = value + 1`** (`botocore/args.py::_compute_retry_max_attempts`). Two consequences for the assertion: `client.meta.config.retries` on a correctly built client carries **no `max_attempts` key at all**, so asserting on that key fails against correct code; and asserting `total_max_attempts == 1` also catches the `max_attempts: 1` mistake, because that config normalises to `total_max_attempts: 2`
   - Verify a **non-retryable** cause fails on the **first** attempt rather than burning the full budget — a denied model (`AccessDeniedException`), an invalid request (`ValidationException`), or an expired credential (`ExpiredTokenException`). Assert the elapsed time is under one backoff cycle, and that `ai_enrichment_error` names that cause's operator action
   - **The `docker compose exec` check is the binding form.** Reading `botocore.config.Config` in the source locally proves the code says it, not that the running service does it — the shipped image, its environment, and its overrides are what answer the call:
     The factory is **`bedrock_runtime_client()` in `backend/utils/bedrock.py`** — the same callable `services/code_parser_service.py` invokes on the enrichment path. There is no `backend/ai_service.py` and there must not be (structure.md names it among the files no task creates), so import it by its real name rather than leaving the reader to substitute one. The Dockerfile copies `backend/` to `/app`, so the in-container import is flat: `from utils.bedrock import bedrock_runtime_client`.
     ```bash
     # Read the config off the client the enrichment path actually constructs.
     docker compose exec backend python -c "from utils.bedrock import bedrock_runtime_client; cfg = bedrock_runtime_client().meta.config; print(cfg.read_timeout, cfg.retries); assert cfg.read_timeout is not None, 'no explicit read_timeout — inheriting the SDK default'; assert cfg.read_timeout > 60, 'read_timeout at or below botocore 60s default; documentation-generation needs 75.1s'; assert cfg.retries.get('total_max_attempts') == 1, 'SDK retries still enabled beneath the explicit policy (a max_attempts:1 config normalises to total_max_attempts:2)'"
     # Expected: exit 0, a read_timeout comfortably above 75.1s, and
     # retries {'total_max_attempts': 1, 'mode': 'standard'} — no max_attempts key present.
     ```

**Pass criteria:** AI enrichment completes with real Bedrock content. Documentation tab shows meaningful rendered markdown. Summary tab shows AI insights above stats. `SKIP_AI_ENRICHMENT=true` yields `skipped` with an error naming the setting, and a forced Bedrock failure yields `failed` — not `skipped` — with an error naming the cause and the operator action, surfaced as an error on both the Summary and Documentation tabs while every deterministic result persists and the analysis reaches 100%. A stage that succeeded before a later stage failed keeps its output stored and rendered, and the recorded error names the failing stage. Inside the running container, the bedrock-runtime client carries an explicit `read_timeout` above botocore's 60s default with the SDK's own retries disabled, and a non-retryable cause fails on the first attempt.

---

## Test 12: AI Enrichment — Response Times and Progress Tracking

Validates that the AI enrichment phase doesn't block the UI and progress is tracked correctly.

**Scenarios:**

1. **Progress tracker shows AI enrichment step**
   - During analysis, verify the progress tracker displays an "AI Enrichment" or "Generating documentation" step
   - Verify progress advances from ~95% to 100% during this phase (not stuck)

2. **Analysis completes within reasonable time**
   - The AI enrichment phase alone completes within **150 seconds**. Measured worst case is 89.8s for the two calls — `analysis-summary` 14.7s (654 output tokens) plus `documentation-generation` 75.1s (4,034 output tokens) — so 150s is a budget with headroom, not an observation
   - Full analysis (clone + parse + AI enrichment) completes within **240 seconds** for the test repo
   - A **60-second** bound on enrichment is the defect encoded as an assertion: `documentation-generation` legitimately needs 75.1s, so a test asserting 60s fails a healthy service and, worse, agrees with botocore's 60s `read_timeout` default that made the call impossible. A bound copied from an SDK default or from an early observation becomes a false failure the moment output grows
   - If either bound trips, **re-measure** the per-call latency against output tokens before widening it. Latency here scales with output size, not prompt size, so a trip is evidence about the workload; widening blindly discards that evidence and re-hides the threshold

3. **Frontend remains responsive during AI enrichment**
   - While analysis is running (specifically during the AI phase), verify:
     - Navigation still works (click other nav items and back)
     - No browser "page unresponsive" warnings
     - Progress updates are visible in the UI

4. **Multiple analyses don't interfere**
   - Start two analyses in quick succession (different repos or same repo)
   - Verify both complete independently
   - Verify each has its own AI documentation (not duplicated from the other)

**Pass criteria:** AI enrichment completes inside its stated budget — 150s for the phase, 240s for the full analysis — bounds set with headroom over the 89.8s measured worst case rather than copied from an SDK default. Progress is visible. UI stays responsive. Concurrent analyses work independently.

---

## Test 13: ATX Analysis — Full Lifecycle

Validates the complete ATX Analysis flow: starting an analysis, live streaming, cancel, and documentation collection.

**Test repository:** `https://github.com/Deenadayaalan/task-manager` (branch: `main`)

**Scenarios:**

1. **Start analysis and verify Cancel button persists**
   - Navigate to http://localhost:3000/atx-analysis
   - Enter the test repo URL
   - Click "Start"
   - Verify the "Cancel" button appears immediately and STAYS visible (does not flash then disappear)
   - Verify the console area shows "Analysis in progress..." spinner
   - Verify the console starts showing content within 30 seconds (init event + ATX CLI output)

2. **Live conversation log streaming (content channel, during the run)**
   - After starting an analysis, wait for the ATX CLI to print its banner (region, trust mode)
   - Verify the console shows the "Conversation log:" path message
   - After that message appears, verify the console starts showing DETAILED content within 5 seconds:
     - Agent reasoning (e.g., "I'll analyze the codebase...")
     - Tool calls (e.g., file reads, shell commands)
     - Progress messages
   - Verify the conversation log arrives on the `log` event channel and de-noised stdout on the `output` channel; both carry the line in the `data` field
   - **Verify log lines arrive WHILE the process is still alive** — capture a log-channel line, then assert the conversation is still `running` (sidebar chip / `GET /conversations/{id}` status) at that moment. This is the regression pin: an implementation that reads the conversation log only after the process exits MUST fail here. Asserting that log content eventually appears does not distinguish the two — the assertion must be ordered against a live process.
   - Verify new lines continue appearing over time (stream stays active)
   - Verify what the console renders contains NO:
     - spinner frames from `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`
     - ANSI escape sequences (`\x1b[` … )
     - pure box-drawing banner lines (lines composed only of `─│┌┐└┘├┤┬┴┼╭╮╰╯━┃█▌▐` and whitespace)
   - Verify real content SURVIVED the de-noising — assert positively that genuine stdout lines are still present (region/trust banner text, the "Conversation log:" line) and that error text, when present, is not filtered away. A filter that drops every line would pass the noise assertions above; only this check catches it.

3. **Cancel stops the analysis**
   - While an analysis is running, click "Cancel"
   - Verify the Cancel button disappears and Start becomes enabled again
   - Verify the console stops receiving new lines
   - Verify the sidebar shows the conversation with "cancelled" status
   - Verify: `docker exec <container> ps aux | grep atx` shows no ATX process for this conversation

4. **Sidebar shows running conversations**
   - Start an analysis
   - Verify the sidebar "Conversations" list updates to show the new entry
   - Verify the entry shows status "running" (with blue chip)
   - After completion, verify status changes to "completed" (green chip)

5. **Click sidebar conversation reconnects to stream**
   - While an analysis is running, click a different nav item then come back
   - Click the running conversation in the sidebar
   - Verify the console reconnects and shows live output (replays stored logs + tails live)
   - Verify the Cancel button appears (since the analysis is still running)
   - Note: this is SPA navigation — the page component is not remounted and no HTTP request is re-issued. It does NOT exercise the reconnect path. Scenario 8 does; both are required.

6. **Completed analysis shows documentation rendered as markdown**
   - Wait for an analysis to complete (status: "completed" in sidebar)
   - Click the completed conversation in the sidebar
   - Switch to the "Documentation" tab
   - Verify `GET /conversations/{id}/docs` returns a non-empty `docs` array AND the conversation `status`, and that every entry carries a `storage_path` (`<id>/docs/<rel>`)
   - Verify each `storage_path` is readable through `GET /file?path=<storage_path>` and returns that document's text
   - Verify the tab renders markdown AS MARKUP — assert a real heading ELEMENT from the document is present (`h1`/`h2` in the DOM), not the literal string `# Heading`. This is what distinguishes rendering from dumping; a `JSON.stringify(doc)` metadata pane passes "files are listed" and fails here.
   - Verify NO metadata leaks into the content pane — assert the rendered output contains no `storage_path`, no `size`, and no JSON-looking blob (`{"` / `":`)
   - Verify selecting a DIFFERENT document in the list loads that document — assert the rendered content changes
   - Verify documents with subdirectory paths (e.g. `architecture/system-overview.md`) are listed and openable, so nested structure is neither flattened nor dropped
   - Reference shape from a real `AWS/comprehensive-codebase-analysis` run: 32 markdown files — `README.md`, `project-overview.md`, `technical-debt-report.md`, plus `architecture/`, `behavior/`, `reference/`, `analysis/`, `technical-debt/`, `migration/`, `diagrams/`, `specialized/` subtrees

7. **SSE keepalive prevents timeout**
   - Start an analysis and leave it running for 5+ minutes
   - Verify the console continues showing new content periodically
   - Verify the Cancel button remains visible throughout
   - Verify no "Stream replay not available" or connection error appears

8. **Browser reload mid-run restores history and resumes live output**
   - Start an analysis and wait until the console holds several lines; record the rendered line count
   - Perform a REAL browser reload: `await page.reload()`. SPA navigation (scenario 5) does not satisfy this scenario — the page must remount.
   - Verify the page issues `GET /atx/conversations/{conversation_id}/stream` after the reload (assert via `page.waitForRequest` / a `page.on('request')` recording, matching the conversation id)
   - Verify prior console output is restored (the recorded lines are present again, not an empty console)
   - Verify the restored events carry `replay: true` — assert on the SSE payloads observed by the page, proving the history came from the reconnect endpoint rather than a re-run of the analysis
   - Verify live output RESUMES: assert the rendered line count grows after the reload (later count > count immediately after reload) within 60 seconds
   - Verify `Waiting for events...` is never displayed at any point after the reload
   - Verify the Cancel button is available again for the still-running conversation, and clicking it still cancels (status becomes `cancelled`)

9. **Client disconnect does not cancel the analysis**
   - Start an analysis via `POST /atx/analyze` (or the page) and abandon the response mid-run — abort the fetch / close the SSE reader while the CLI is still working. A page refresh is exactly this disconnect.
   - Reconnect with `GET /atx/conversations/{conversation_id}/stream`
   - Verify the conversation status is still `running` (not `cancelled`, not `error`) and that NEW events arrive after the reconnect, proving the work outlived the client
   - Verify `docker exec <container> ps aux | grep atx` still shows the ATX process for this conversation

10. **Stale `running` conversation is reconciled after an agent restart**
    - Start an analysis and wait until the sidebar shows it `running`
    - Restart the agent: `docker compose restart atx-analysis-agent`
    - Attach to that conversation's stream: `GET /atx/conversations/{conversation_id}/stream`
    - Verify the stream TERMINATES with an `error`/`interrupted` event carrying a message — it must not hang. An eternal wait is the same user-visible defect as a stuck console, reached from the other direction, and a timeout-based pass is not acceptable here.
    - Verify the sidebar stops showing the conversation as `running` (status becomes `interrupted`) and the Cancel button is not offered for it
    - Verify `Waiting for events...` is never displayed

11. **Documentation tab distinguishes its empty states**
    - A COMPLETED conversation that produced no documents: verify the tab says specifically that the analysis completed without producing documentation
    - A STILL-RUNNING conversation: verify the tab says the analysis is still running — not that it produced nothing
    - A LOAD FAILURE (`docker compose stop atx-analysis-agent`, then open the tab): verify the tab says it could not load the documentation, with a reason — not that documents are absent. Restart the agent afterwards (`docker compose start atx-analysis-agent`).
    - Verify these three messages are DISTINGUISHABLE FROM EACH OTHER in the rendered text. A single shared catch-all message fails this scenario — it is what made an empty Documentation tab indistinguishable from a working one.
    - Note: collection is retried on read when `docs/` is empty, so a conversation stranded by an agent restart repairs itself on first open; "completed with no documentation" must survive that retry before it is reported.

12. **Internal links in generated documentation are followable**
    - From a completed run's Documentation tab, follow a relative link in `README.md` — verify it **selects another document in the panel** and does not open a new browser tab
    - Verify no anchor in rendered generated documentation carries `target="_blank"` for a relative href. The handler intercepted only `#` hrefs; everything else went to `target="_blank"`, so the ATX `README.md`'s links to its 31 siblings opened URLs the SPA cannot serve
    - Verify a sibling link written **bare inside a subdirectory document** resolves within that subdirectory: `architecture/dependencies.md` linking `system-overview.md` opens `architecture/system-overview.md`. Resolution is against the open document's own directory — `components.md` and `patterns.md` link the same name bare, and root-relative resolution finds nothing for any of them
    - Verify a `#fragment` on a cross-document link both selects the document AND scrolls to the heading. The scroll happens after the new markup commits; a synchronous scroll against the previous document's markup is a FAILURE that looks identical to a broken anchor
    - Verify an unresolvable relative link renders as a **non-navigating element whose accessible text names the target** — not as a link that opens a dead tab, and not as one that silently does nothing
    - Verify a heading whose text contains a code span or a bold run has an id derived from its **rendered text**, with no `[object Object]`. `String(children)` produced those ids, so no anchor could ever match
    - Verify the slug rule matches GitHub's: `Installation & Setup` is `installation--setup`, two hyphens. Measured across 11 real analyses, 14 of 34 ToC anchors resolved before
    - Verify repo-wide that **exactly one** markdown link-resolution module exists and every markdown surface imports it. Three copies had drifted to three different slug rules
    - Reference measurement against a real 32-document run: 111 internal links, 108 resolving. The residual failures render visibly unfollowable rather than looking like working links

**Pass criteria:** Analysis starts and Cancel button stays visible. Console shows live conversation log content on the `log` channel while the process is still running, with spinner/ANSI/box-drawing noise removed and real content intact. Cancel kills the process. A browser reload restores history as `replay: true` events and resumes live output without ever showing `Waiting for events...`. A client disconnect leaves the analysis running. An agent restart reconciles stale `running` conversations to a terminal event instead of hanging. Sidebar reflects real-time status. Documentation is collected after completion, served with readable content, and rendered as markdown with no metadata in the content pane; the empty, running, and load-failure states are distinguishable. Relative links inside the generated documents navigate within the panel, resolve against the containing document's directory, and render visibly unfollowable when the destination is unknown.

**API verification:**
```bash
# Start analysis via API
curl -s -N -X POST http://localhost:8004/analyze \
  -H "Content-Type: application/json" \
  -d '{"repository_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main", "analysis_type": "code-assessment"}' | head -5
# Expected: First line is data: {"type": "init", "conversation_id": "atx_..."}
# Subsequent lines are data: {"type": "output"|"log", "data": "..."} — `log` carries the
# ATX conversation log, `output` carries de-noised stdout

# Cancel via API
curl -s -X POST http://localhost:8004/cancel/<conversation_id> | jq .
# Expected: {"status": "cancelled", "conversation_id": "..."}

# Stream reconnect
curl -s -N http://localhost:8004/conversations/<conversation_id>/stream | head -5
# Expected: SSE events with replay:true flag for completed, or live content for running

# Mid-run reconnect — replay-then-live transition (run while the analysis is still going)
curl -s -N http://localhost:8004/conversations/<conversation_id>/stream | head -30
# Expected: persisted events flagged "replay": true, then live events with no replay flag

# Client disconnect must not cancel the analysis: abandon the /analyze read above,
# then confirm the work is still progressing
curl -s http://localhost:8004/conversations/<conversation_id> | jq .status
# Expected: "running" (not "cancelled")

# Stale running reconciliation — restart the agent mid-analysis, then attach
docker compose restart atx-analysis-agent
curl -s -N --max-time 30 http://localhost:8004/conversations/<conversation_id>/stream | tail -5
# Expected: a terminal error/interrupted event, and the stream closes. Hanging until
# --max-time is a FAILURE, not a pass.

# Documentation listing — count, conversation status, and sample names
curl -s http://localhost:8004/conversations/<conversation_id>/docs | jq '{count: (.docs|length), status: .status, sample: [.docs[0:3][].name]}'
# Expected: count > 0 for a completed analysis that produced documentation, status "completed"

# Document content — served from the storage_path in the listing above
curl -s "http://localhost:8004/file?path=<storage_path from above>" | jq -r .content | head -5
# Expected: the document's markdown text
```

`count: 0` on a `completed` analysis means collection looked in the wrong place — the CLI writes `ATXDocumentation/` into the project path passed to `-p` (the cloned repo at `<storage>/<id>/repo`) and mirrors it under its own run dir `~/.aws/atx/custom/<run_id>/`, not into the process cwd.

---

## Test 14: ATX Transform — Full Lifecycle

Validates the ATX Transform flow: starting a transformation, live streaming, history tracking, and diff viewing.

**Test repository:** `https://github.com/Deenadayaalan/task-manager` (branch: `main`)
**Transformation type:** `AWS/java-version-upgrade`

**Console content note:** `output.log` now holds DE-NOISED lines — ANSI escapes stripped, spinner frames and pure box-drawing banner lines dropped, progress repaints collapsed. Assertions about console content must expect readable text and NO spinner frames from `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`, while still asserting positively that real content survived (region/trust banner text, agent activity) and that error text is not filtered away. A filter that dropped every line would satisfy the noise assertions alone.

**Payload discriminator note:** every SSE payload on a transform stream carries its `type` inside the `data:` JSON. Reading the SSE `event:` name does NOT count as reading the type — the shared `streamSSE` client discards `event:` lines, so a stream that distinguishes its events only by `event:` name looks correct in a raw `curl` and arrives undiscriminated at the page. Assert on the parsed `data:` payload, as the page sees it.

**Scenarios:**

1. **Start transformation and verify live streaming**
   - Navigate to http://localhost:3000/atx-transform
   - Enter the test repo URL and branch
   - Select "AWS/java-version-upgrade" from the dropdown (or first available)
   - Click "Start Transform"
   - Verify: console shows "Transformation in progress..." immediately
   - Verify: console starts showing ATX CLI output (region banner, tool trust, then agent activity)
   - Verify: the Cancel button (or Stop) appears and stays visible while running

2. **Transformation history updates in sidebar**
   - After starting a transformation, verify the "Transformations" sidebar list shows the new entry
   - Verify the entry shows status "running" or "transforming"
   - After completion, verify status changes to "completed"
   - Verify: repo URL, branch, and transformation type are visible in the list entry

3. **Click sidebar item reconnects to stream**
   - While a transformation is running, navigate away and return
   - Click the running transformation in the sidebar
   - Verify: console shows live output (not "Stream replay not available")
   - Verify: for completed transformations, clicking shows the full replayed log

4. **Transformation completes successfully and the UI leaves its in-progress state**
   - Wait for the transformation to finish
   - Verify: the LAST payload on the stream is terminal, read from the `data:` JSON — `{"type": "complete", "status": ...}` on success, or `{"type": "error", "message": ...}` on failure. An `error` payload MUST carry a non-empty `message`, not merely a status.
   - Verify: EVERY payload observed on the stream carries a `type` field in its `data:` JSON (see the payload discriminator note above — the `event:` name does not count)
   - Verify: **the in-progress state actually clears on the page** — after the terminal event, the "Transformation in progress..." indicator is gone and the Start/Transform control is enabled again. Do NOT accept the sidebar status chip as evidence of this: the chip is fed by `GET /transformation-history`, an independent poll that flips to `completed` whether or not the stream ever terminated. Substituting the chip for the page state is exactly how a permanently stuck spinner passed this test.
   - Verify: the history sidebar refresh that hangs off the terminal branch FIRES — assert the history list actually updates after completion (the entry's status/contents change on the page following the terminal event, without a manual reload)
   - Verify: sidebar status changes to "completed"
   - Verify: the diff endpoint returns data:
     ```bash
     curl -s http://localhost:8005/diff/<repo_id> | jq '.files | length'
     # Expected: >= 0 (number of changed files)
     ```

5. **Transformation type dropdown submits CLI identifiers, not display labels**
   - Verify the transformation type dropdown has options loaded
   - Verify at least the AWS managed transformations appear (java-version-upgrade, etc.)
   - Verify: `curl -s http://localhost:8005/transformations | jq '.definitions | length'` returns >= 1
   - Verify: the list under `.definitions` is **flat** — every element is an object, none is a nested array. The backend's definition CRUD writes one `definitions.json` holding a JSON array; appending that file's parsed contents as a single element produces a nested list plus a blank dropdown row.
   - Verify: every entry carries `atx_definition_name`, the resolved CLI identifier
   - Verify: AWS-managed entries resolve `atx_definition_name` to their `id` (`AWS/java-version-upgrade`), custom entries to their `name`. A custom record's `id` is a local `uuid4` the CLI has never heard of, so submitting `id` universally fails with "definition not found" instead of a `ValidationException` — a different error, equally broken.
   - Verify: every non-null `atx_definition_name` matches the ATX `resource` pattern — optional `AWS/` prefix, then alphanumeric segments joined by single `.`, `_` or `-`, at most 64 characters after the prefix
   - Verify **from the page**: select an option, click Start, and assert the outgoing request body's `transformation_type` equals the CLI identifier, NOT the display label. That request body is the seam that broke — a dropdown offering correct-looking labels while submitting `Java Version Upgrade` passes any options-are-loaded check. The visible option text must still be the human label.
   - Verify: an entry whose `atx_definition_name` is `null` renders **disabled** and cannot be submitted
   - Verify: the dropdown has zero options with an empty or `undefined` value

6. **Stream endpoint handles 404 gracefully**
   - Click a very old or nonexistent transformation ID in a crafted URL
   - Verify: console shows "Stream replay not available" (graceful fallback, not crash)
   - Verify: no unhandled JavaScript errors in browser console

7. **Multiple concurrent transformations**
   - Start a transformation, then immediately start another with a different repo/branch
   - Verify: both appear in the sidebar
   - Verify: clicking between them shows the correct output for each
   - Verify: each runs independently (one completing doesn't affect the other)

8. **Stream must not terminate before the job leaves `running`**
   - Start a transformation and IMMEDIATELY attach to its stream (`GET /conversations/{repo_id}/stream`) — attach inside the pre-launch clone window, while the history record exists but no subprocess does yet
   - Verify: NO terminal event (`type: "complete"` or `type: "error"`) arrives while `GET /transformation-history` still reports that record as `running`
   - Verify: no terminal payload ever carries `status: "running"`. A terminal event whose own payload says `running` is a FAILURE — it is self-contradictory and it is what a tail loop keyed on process liveness emits during the clone window.
   - Verify: the stream stays open and subsequently delivers `output` lines once the CLI starts, then a terminal event only after the record has left `running`

9. **Reconnect to an already-finished transformation replays, terminates, and stops spinning**
   - Wait for a transformation to reach `completed`, then attach fresh (real page load or `GET /conversations/{repo_id}/stream`)
   - Verify: stored lines are replayed flagged `replay: true` in the `data:` payload
   - Verify: the stream ENDS on a terminal event (`complete`/`error`) rather than hanging
   - Verify: the page ENTERS then LEAVES its in-progress state — the "Transformation in progress..." indicator appears during replay and is gone once the terminal event arrives, with the Start/Transform control enabled again. It must not stay spinning on a run that finished long ago.

10. **Invalid `transformation_type` is rejected before the CLI runs (fail-fast guard)**
    - `POST /transform` with the display label `"Java Version Upgrade"` → 422, and the response message names the rejected value
    - `POST /transform` with `"AWS/java-version-upgrade"` → not 422
    - `POST /transform` with the package ARN form `arn:aws:transform-custom:us-east-1:123456789012:package/AWS/java-version-upgrade` → not 422
    - `POST /transform` with a trailing newline (`"AWS/java-version-upgrade\n"`) → 422. Matching must use `fullmatch`; Python's `$` matches before a final newline, so a `match`-based check would admit it.
    - Why this matters: without the guard the caller gets 200 plus a `repo_id`, and the real failure surfaces minutes later as an opaque `ValidationException` on `resource` in the tail of `output.log`, with no signal at the call site.

11. **Results page is reachable by navigation, not by URL**
    - Start on http://localhost:3000/atx-transform with at least one `completed` transformation in the history sidebar
    - Click the completed history row
    - Verify: the URL becomes `/transform-results/{repo_id}` and the diff view renders
    - Verify: the completed row still exposes an explicit "replay console" action, and a row in any other status replays the console instead of navigating
    - Reaching `/transform-results/:id` with `page.goto()` does NOT discharge this scenario (Rule 16). Typing the URL is exactly what passed while the page was dead code — the route was registered, rendered correctly, and nothing repo-wide navigated to it.
    - Verify repo-wide: every route registered in `App.tsx` has at least one inbound navigation path from the running UI — a nav item, a link, or a `navigate()` call reachable from a rendered page. A route with no inbound path is a FAILURE even when the component renders.

12. **Changed files render from the agent's shape**
    - Verify: `GET /diff/{repo_id}` returns `{repo_id, files, truncated, omitted_files}`, each file `{filename, status, lines, truncated}`, each line `{type, content, old_line_number, new_line_number}` with `type ∈ {added, removed, unchanged}`
    - Verify: the payload carries **no** `path`, `before`, `after` or `diff` key. `EnhancedFileComparison` casts the response and defaults missing fields, so the old shape renders every file as "unknown" with zero rows instead of throwing — asserting "the page did not crash" passes against the mismatch.
      ```bash
      curl -s http://localhost:8005/diff/<repo_id> | jq '{top: (keys), legacy: [.files[0]|keys[]|select(.=="path" or .=="before" or .=="after" or .=="diff")], first: (.files[0]|{filename, status, lines: (.lines|length)}), line: .files[0].lines[0]}'
      # Expected: legacy [] (empty). filename non-empty, lines > 0, line carries type/content/old_line_number/new_line_number.
      # A "path" key or a null filename is the shipped defect.
      ```
    - On the page: verify the file tab strip is keyed on real filenames — assert no tab reads `unknown`, and that a tab's label matches a `filename` from the payload
    - Verify: a `modified` file shows BOTH added and removed rows (`replace` opcodes emit removed-then-added)
    - Verify: runs of unchanged lines collapse into a "show N unchanged lines" control rather than rendering in full
    - Verify: truncation is **stated** when a bound trips — a file past 2,000 lines, a response past 20,000 lines, or files past 300 changed files carries `truncated: true` / `omitted_files` and the page says so. Silently dropping content passes any "rows rendered" assertion.
    - Verify: a **long line's full content is present in the DOM.** Pick the longest `content` string in the payload (or a file with a minified line, a base64 blob or a long URL) and assert the rendered row's text contains that string **in its entirety**, character for character, with its leading whitespace. Asserting that the row "renders", or that it is visible, or that it contains the line's first N characters, is insufficient and is exactly what passed against the shipped defect: `whiteSpace: 'pre'` with `overflow: hidden` and `textOverflow: 'ellipsis'` produced a row that rendered, was visible, and had the remainder of every long line unreachable — not scrollable, not selectable, not copyable. A clipped row looks complete, so only the full-content assertion can tell the two apart.
    - Verify: the diff pane does **not** scroll horizontally — its `scrollWidth` does not exceed its `clientWidth`. Horizontal scrolling is not the accepted alternative to wrapping; the row is the unit being read.
    - Verify: a wrapped row's line number aligns to its **first** visual line and is not repeated per visual line, and the row's added/removed/unchanged background covers its **full** wrapped height. A correct wrap under gutters and backgrounds sized for one-line rows still looks broken, and that appearance is a FAILURE, not a cosmetic note.

13. **Summary reports the size of the change, not the size of the repository**
    - Verify: `GET /diff-summary/{repo_id}` emits `changed_files`, `additions` and `deletions` alongside `total_files` and the per-status counts
    - Verify: `additions` and `deletions` are non-zero for a run with real edits, and are computed uncapped — a truncated diff view never understates the change
    - Verify: the page header does NOT display `total_files` as "files changed". A header reading the repository's file count looks entirely plausible and is what shipped; assert the rendered count equals `changed_files`.
    - Verify: neither figure renders as `undefined` — the header previously read `summary.additions`/`summary.deletions`, which the summary never emitted
    - Reference run `e9b17e82-ea4`: 5 modified files, 16 additions, 7 deletions, against a `total_files` of 49. A header reading 49 is the defect.
      ```bash
      curl -s http://localhost:8005/diff-summary/<repo_id> | jq '{changed_files, additions, deletions, total_files}'
      # Expected: all four present; changed_files ≤ total_files; additions/deletions > 0 for a run with edits
      ```

14. **Whole-tree download**
    - Verify: `GET /download/{repo_id}` returns a zip of the ENTIRE transformed tree from `<storage>/<repo_id>/repo` — not only the changed files, and never `original/`, which is the diff baseline
    - Verify: `.git` is excluded and `.gitignore` retained
    - Verify: the response carries **no `Content-Length`** and arrives chunked. That is the observable proof it streams; a `Content-Length` header means the whole archive was assembled in memory first.
      ```bash
      curl -s -D - -o /tmp/transformed.zip http://localhost:8005/download/<repo_id> | grep -iE 'content-length|transfer-encoding'
      # Expected: Transfer-Encoding: chunked, and NO Content-Length. A Content-Length is a FAILURE.
      unzip -l /tmp/transformed.zip | grep -c '\.git/' # Expected: 0
      unzip -l /tmp/transformed.zip | grep -c '\.gitignore' # Expected: >= 1 if the repo has one
      ```
    - Verify: an over-cap tree is a **413 naming the 500MB limit**, and an unknown `repo_id` a 404 — both BEFORE any bytes are sent. Once headers are committed, a limit discovered mid-stream can only be expressed as an archive that opens cleanly and is missing files.
      ```bash
      curl -s -o /dev/null -w "%{http_code}" http://localhost:8005/download/does-not-exist
      # Expected: 404 (not a 200 followed by an exception)
      ```
    - Verify: the download action is offered even when the diff has nothing to show — a documentation-only transformation still produces output worth having
    - Verify: no archive member escapes the repo root — a `..` segment or a symlink resolving outside `<storage>/<repo_id>/repo` is refused rather than followed, and no content from outside that tree appears in the archive

15. **Records survive a restart**
    - With at least one `completed` transformation present, restart the agent and wait for health:
      ```bash
      docker compose restart atx-transform-agent
      until curl -sf http://localhost:8005/health >/dev/null; do sleep 2; done

      curl -s http://localhost:8005/transformation-history | jq '.records | length'   # Expected: >= 1, includes <repo_id>
      curl -s -o /dev/null -w "diff %{http_code}\n"    http://localhost:8005/diff/<repo_id>
      curl -s -o /dev/null -w "summary %{http_code}\n" http://localhost:8005/diff-summary/<repo_id>
      curl -s -o /dev/null -w "dl %{http_code}\n"      http://localhost:8005/download/<repo_id>
      # Expected: all non-404. A listed record whose every action 404s is the shipped defect —
      # the history survived because the sidebar re-polls, the module-level dict did not.
      ```
    - Reading the history twice inside one process proves nothing (Rule 17) — the process dict answers both times. The restart is the assertion.
    - Verify: history is ordered newest-first by `created_at`, not by filesystem iteration order
    - Verify: a storage directory with neither readable metadata nor a `repo/` tree is ABSENT from the listing — a row whose every action 404s is worse than no row
    - Verify: a record left `running` with nothing tracked in this process is reconciled to `interrupted`, and its stream emits a terminal event rather than polling forever
    - Verify: a backfilled record (trees on disk, no `metadata.json`) carries `repo_id` from the directory name, `created_at` flagged `created_at_source: "filesystem"`, `status: "unknown"`, `backfilled: true`, and `repo_url`/`branch`/`transformation_type` present and **`null`** — never guessed
    - Verify **against the API, not the page**: `POST /create-file-pr/{repo_id}` on a backfilled record returns **400 explaining why** (a null `repo_url` is no remote to push a branch to), while `/diff` and `/download` work normally, because both derive from the trees. PR creation is an API-only capability — the results page offers no PR button, dialog or preview — so there is no page state to assert here and no scenario in this document may assert one
    - Verify: re-reading a backfilled record leaves it unchanged — the repair is persisted once and is idempotent

16. **The result states each kind of output separately, including zeros**
    - Verify: every entry of `GET /diff/{repo_id}` carries a `category` of `"source"` or `"documentation"`
    - Verify: `GET /diff-summary/{repo_id}` carries `source_files_changed`, `documentation_files_changed` and `changed_by_category`, **whose keys are present even when the value is `0`**. Assert PRESENCE, not truthiness — a `> 0` assertion is vacuous on exactly the count that matters, and an absent key and a zero are different facts
    - Verify: the two counts SUM to `changed_files`
    - Verify: the page states BOTH counts unconditionally, and for a documentation-only run says the run generated documentation and made no source changes — never a bare "32 files changed" that reads as missing source edits
    - Verify: a documentation-only run keeps all 32 documents viewable — nothing is filtered on account of category. `category` labels the output; it does not gate it
    - Reference figures: `AWS/comprehensive-codebase-analysis` → 32 documentation / 0 source; the cross-check run → 5 source / 0 documentation, 16 additions, 7 deletions
      ```bash
      curl -s http://localhost:8005/diff-summary/<repo_id> | jq '{has_source: has("source_files_changed"), has_docs: has("documentation_files_changed"), source_files_changed, documentation_files_changed, changed_files, changed_by_category}'
      # Expected: has_source and has_docs both true, INCLUDING on a run where one of them is 0.
      # source_files_changed + documentation_files_changed == changed_files.
      curl -s http://localhost:8005/diff/<repo_id> | jq '[.files[] | select(has("category") | not)] | length'
      # Expected: 0. An entry with no category is the defect.
      ```

17. **Changed-file navigation is a listbox, not a tab strip**
    - Verify: the changed-file navigation is a `role="listbox"` with one `role="option"` per entry and `aria-selected` on the current one — **not** a `role="tab"` strip
    - Verify: each option's accessible name carries the **full relative path**, not a basename. `src/main/App.java` and `src/test/App.java` are distinguished by nothing else
    - Verify: options are grouped by category and each group heading carries its own count
    - Verify: exactly one option is focusable at a time (roving tabindex), and Up/Down/Home/End move the selection
    - Verify: selecting an option switches the diff pane to that file
    - The previous horizontal strip truncated filenames, so an assertion on a visible tab label could pass while the label was unreadable

**Pass criteria:** Transformation starts and streams live de-noised ATX output to console. Every payload carries `type` in its `data:` JSON; the stream ends on a terminal `complete`/`error` payload and only after the record has left `running`. The page's in-progress indicator clears on that terminal event and the history sidebar refreshes off it — verified on the page, not via the `transformation-history` status chip. History sidebar shows real transformations (not placeholder data). Stream reconnection works for both running and completed transformations, and a completed run never leaves the console spinning. The dropdown loads a flat catalog whose every option submits a resolved CLI identifier rather than a display label, and `POST /transform` rejects an invalid `transformation_type` with 422 before the CLI runs. A completed run's results page is reachable by CLICKING its history row, not only by typing the URL, and every registered route has an inbound navigation path. The diff renders from `filename` and `lines[]` with no `path`/`before`/`after`/`diff` keys, modified files showing both added and removed rows, and truncation stated rather than silent. Long lines **wrap**: the longest line's full content is present in the DOM, the diff pane does not scroll horizontally, and no row is clipped with an ellipsis. The download is the results page's only action — no PR button, dialog or preview is present, and PR creation is asserted against the agent's API instead. The summary reports the change — `changed_files`, `additions`, `deletions` — and never presents `total_files` as files changed. The download streams the whole transformed tree chunked with no `Content-Length`, excludes `.git`, keeps `.gitignore`, and answers an over-cap tree with a 413 naming the 500MB limit before sending bytes. Every diff entry carries a `category`, the summary carries `source_files_changed`, `documentation_files_changed` and `changed_by_category` with their keys present at zero and summing to `changed_files`, and the page states both counts unconditionally. Changed files are navigated by a keyboard-navigable listbox labelled with full relative paths and grouped by category with per-group counts. Every record and its `/diff`, `/diff-summary` and `/download` artefacts survive `docker compose restart atx-transform-agent`.

**API verification:**
```bash
# Health check
curl -s http://localhost:8005/health | jq .
# Expected: {"status": "healthy", ...}

# Start transformation — caller-supplied configuration. This form MUST keep working:
# a caller value always wins, verbatim, and is never merged with the agent's default.
curl -s -X POST http://localhost:8005/transform \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main", "transformation_type": "AWS/java-version-upgrade", "configuration": "additionalPlanContext=The target Java version is Java 21"}' | jq .
# Expected: {"repo_id": "task-manager_main", "status": "running", ...}
# The record records the origin: configuration_source "request".

# Start transformation — configuration OMITTED. Proves the agent's default path.
curl -s -X POST http://localhost:8005/transform \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main", "transformation_type": "AWS/java-version-upgrade"}' | jq .
# Expected: same shape. The run must get past CLI startup WITHOUT the
# "non-interactive mode requires the --configuration (or -g) input" error, and the
# console must state that a default was applied and name it (Test 15 scenario 4b).

# Stream (connect to live output)
curl -s -N http://localhost:8005/conversations/task-manager_main/stream | head -5
# Expected: log lines as data: {"type": "output", "data": "[ISO timestamp] line", "replay": true}
# on replay, and the same shape WITHOUT the `replay` key when live. Every payload carries
# `type` inside the data: JSON — the SSE `event:` name is discarded by the shared client
# and does not discharge this.

# Terminal event — the last payload must be terminal, and never `status: "running"`
curl -s -N --max-time 600 http://localhost:8005/conversations/task-manager_main/stream | tail -3
# Expected: data: {"type": "complete", "status": "completed"} on success, or
# data: {"type": "error", "message": "<non-empty>"} on failure. Hanging until --max-time
# is a FAILURE. A terminal payload carrying "status": "running" is a FAILURE.

# Transformation history
curl -s http://localhost:8005/transformation-history | jq '.records | length'
# Expected: >= 1

# Available transformations
curl -s http://localhost:8005/transformations | jq '.definitions[0].name'
# Expected: A transformation name string

# Every catalog entry exposes a resolved CLI identifier, and the list is flat
curl -s http://localhost:8005/transformations | jq '{count: (.definitions|length), nested: [.definitions[]|select(type=="array")]|length, missing_identifier: [.definitions[]|select(.atx_definition_name==null)|.name]}'
# Expected: count >= 13, nested 0. Names under missing_identifier cannot be executed and
# must render disabled in the dropdown.

# The guard rejects a display label before the CLI runs
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8005/transform \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/Deenadayaalan/task-manager", "transformation_type": "Java Version Upgrade"}'
# Expected: 422. A 200 here means the guard is missing and the transformation will fail
# later with ValidationException on 'resource'.
```

**Key names matter here.** `GET /transformation-history` returns `{"records": [...]}`, which is pinned by **Build Constraint 33**. `GET /transformations` returns **`{"definitions": [...]}`** — asserted here, because **no Build Constraint currently pins it.** BC 33 covers only `/conversations`→`{"conversations": …}` and `/transformation-history`→`{"records": …}`; BC 7's `.definitions` entry is for the *backend* CRUD route `/transformations/definitions`, a different endpoint on a different service. Citing either for the agent catalog makes an unpinned key look covered, so the shape is stated outright: the agent's catalog envelope key is `definitions`, and every scenario in Test 14 that reads `.definitions` depends on it. This needs a constraint amendment to become binding — until then this line is the only statement of it.

A `null` from `jq` means the key name is wrong, not that the list is empty — a scripted check comparing `null` against "at least 1" either fails confusingly or passes vacuously.

**Running Test 14 end to end:** `AWS/java-version-upgrade` no longer needs a caller-supplied `configuration` — the agent supplies the Java 21 default (design doc "Transformation Configuration Defaults"). It still fails against the test repository, but for a **third reason**, and the three MUST NOT be blurred into one another: a missing configuration, a repository with nothing to transform, and a missing build toolchain are different failures — conflating the first two is what let the missing default read as expected behaviour.

The test repository **does** contain a Java Spring Boot backend, and the transformation runs through planning into execution and makes real edits: `java.version` `1.8` → `21` in `pom.xml`, `javax.*` → `jakarta.*`, Dockerfile base image `eclipse-temurin-8` → `21`. It fails because **the transform agent image contains no JDK and no Maven**, so the run cannot execute its verification build — the CLI's own log records `which mvn` returning nothing and `java -version` reporting not-found, concludes `EXTERNALLY BLOCKED — No Java 21 runtime or Maven is installed in this environment`, and ends `## OVERALL STATUS: INCOMPLETE`. Recorded as an open question in design doc "Runtime Prerequisite Verification — the ATX CLI"; nothing here obliges the image to carry a JDK or Maven.

Two consequences for what this test may assert. A console with no `additionalPlanContext` startup error but a toolchain block **discharges** scenario 4b of Test 15 — the default was applied. And the record for that run reads `status: "completed", exit_code: 0` even though the CLI reported `FAILURE`, which is the known status-fidelity defect in design doc "Transformation Record Persistence": do not read a `completed` status on this run as evidence the transformation succeeded.

`AWS/nodejs-version-upgrade` has **no** registered default and does still require `configuration` with `additionalPlanContext`. `AWS/comprehensive-codebase-analysis` is config-free, needs no target-language toolchain, and is valid for any codebase, so it remains the practical choice for proving `resource` validation passes.

---

## Test 15: ATX Pages — Error Handling & Edge Cases

Validates graceful handling of errors and edge cases on both ATX pages.

**Scenarios:**

1. **Invalid repository URL**
   - On ATX Analysis page, enter an invalid URL (e.g., "not-a-url")
   - Click Start
   - Verify: error appears in console (not a crash or frozen UI)
   - Verify: Start button becomes enabled again after error

2. **Private repository without PAT**
   - Enter a private GitHub repo URL (without providing a PAT token)
   - Click Start
   - Verify: clone fails with meaningful error message in console
   - Verify: conversation appears in sidebar with "failed" status

3. **ATX agent container down**
   - Stop the atx-analysis-agent: `docker compose stop atx-analysis-agent`
   - Try to start an analysis from the frontend
   - Verify: error message appears (502 or connection refused), no infinite spinner
   - Restart: `docker compose start atx-analysis-agent`

4. **Missing configuration — the error path AND the defaulted path**

   Both halves are required. Design doc "Transformation Configuration Defaults (`-g additionalPlanContext`)" registers a default for `AWS/java-version-upgrade` (Java 21) and **deliberately none** for the other version-upgrade definitions, so the two definitions have different correct outcomes and each needs its own assertion (Rule 22).

   **4a. A definition with no registered default still fails, by name.** Start an `AWS/nodejs-version-upgrade` transform WITHOUT `configuration`.
   - Verify: the ATX CLI's startup error appears in the console log — `non-interactive mode requires the --configuration (or -g) input`, naming the `additionalPlanContext` section
   - Verify: the transformation shows `failed` status with error details
   - The requirement behind this assertion: no default is registered for this definition, so the CLI's refusal **is** the correct behaviour until a target version is chosen for it. Keeping only the positive case below would lose coverage of this error surface entirely

   **4b. A definition with a registered default gets past CLI startup.** Start `AWS/java-version-upgrade` WITHOUT `configuration`.
   - Verify: the console does **NOT** contain that startup error — the run proceeds and reaches planning and editing. It may still fail later, and against this repo it does: the agent image carries no JDK and no Maven, so the transformation cannot run its verification build (see the Test 14 end-to-end note). A missing configuration and a missing build toolchain are different failures and this assertion is only about the first
   - Verify: the console **states that a default configuration was applied and names it** (Java 21). The notice is written into `output.log` through the same de-noised write path as every other line, so assert it on a **reconnect replay** as well as live — appearing in one and not the other is a failure
   - Verify: the persisted record carries the effective `configuration` and `configuration_source: "agent-default"` — the value names *who supplied it*, so the agent's hardcoded default and a future catalog-declared one stay distinguishable. Read the record in the container — `GET /transformation-history`'s response shape is fixed by Build Constraint 33 and does not carry it
   - This scenario previously asserted the **opposite** — the `java-version-upgrade` startup failure as expected — while the design required the default. That is the wrong-test defect Build Constraint 78 and Rule 22 describe
     ```bash
     REPO_ID=$(curl -s -X POST http://localhost:8005/transform -H 'Content-Type: application/json' \
       -d '{"repo_url": "https://github.com/Deenadayaalan/task-manager", "branch": "main", "transformation_type": "AWS/java-version-upgrade"}' | jq -r .repo_id)

     curl -s -N --max-time 300 "http://localhost:8005/conversations/$REPO_ID/stream" \
       | grep -c 'non-interactive mode requires'
     # Expected: 0. A non-zero count means no default was applied — the shipped defect.

     curl -s -N --max-time 300 "http://localhost:8005/conversations/$REPO_ID/stream" \
       | grep -ci 'default configuration'
     # Expected: >= 1, and the matched line names Java 21. A default applied without a
     # visible notice is a silent rewrite of the transformation's target version.

     docker compose exec atx-transform-agent \
       cat "/app/storage/$REPO_ID/metadata.json" | jq '{configuration, configuration_source}'
     # Expected: configuration non-null, configuration_source "agent-default".
     ```

5. **Rapid start/cancel/start cycle**
   - Start an analysis, immediately cancel, then start again
   - Verify: no zombie processes, second analysis starts cleanly
   - Verify: sidebar shows correct status for each attempt

6. **Browser refresh during running analysis**
   - Start an analysis, then refresh the browser (F5)
   - Navigate back to ATX Analysis page
   - Verify: the running conversation appears in sidebar with "running" status
   - Click it → verify stream reconnects and shows live output

**Pass criteria:** All error cases show meaningful messages. No frozen UI or unrecoverable states. Recovery works after errors.

---

## Common Issues to Fix

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Under construction" on a page | App.tsx routes still using PlaceholderPage | Import and wire real component |
| Nav clicks don't change page | Navigation not using `useNavigate()` | Add router navigation on click |
| `/api/analysis//summary` 404 | Empty analysisId passed to API | Guard with `if (!analysisId)` check |
| `CORS_ORIGINS` parse error | `list[str]` field receiving plain string from env | Use `str` field + `.split(",")` property |
| `ruff not installed` in hooks | Hook calling `ruff` directly | Use `make lint` instead |
| `@mui/x-tree-view` peer dep conflict | Version incompatible with MUI 5 | Pin to `^7.0.0` |
| 422 on `/api/analyze/github` | Frontend sends `github_url` instead of `repo_url` | Use exact field names from `backend/models.py` Pydantic models (`GithubAnalysisRequest`) |
| `TypeError: data.map is not a function` on result tab | `api.ts` returns wrapped response `{"file_stats": [...]}` instead of the inner array | Unwrap with `response.data.file_stats ?? []` — see Build Constraint 7, which lists `/file-stats`→`.file_stats` explicitly. (Build Constraint 16 is defensive type guards for dual backend formats, a different problem) |
| Result tab shows "No data available" when data exists | `api.ts` returns the envelope object, component's type guard rejects it | Check the endpoint response shape and unwrap the correct key |
| Documentation tab shows "No AI documentation available" | AI enrichment failed silently or credentials expired | Check `ai_enrichment_status` in summary response — if "skipped", check `.env` AWS credentials and Bedrock model access |
| AI enrichment reports `skipped` on every analysis, or documentation is absent while the analysis completes | botocore's inherited 60s read timeout is shorter than the documentation call needs (75.1s measured), and every exception was classified `skipped` | Set `read_timeout` explicitly from measured output-token latency, disable the SDK's own retries beneath the explicit policy, classify an attempted-and-raised call as `failed` with a cause naming the operator action, and persist each model result as it arrives |
| Cancel button flashes then disappears | SSE stream closing immediately after start | Check ATX agent logs for errors; verify `stream_with_init()` wrapper emits init event before `run_analysis` generator |
| Console shows only spinners/banner noise, no agent output | Raw stdout streamed straight through, and the ATX conversation log only read after the process exits | De-noise stdout (strip ANSI, drop spinner and box-drawing frames) onto `output`; tail the conversation log file concurrently with the running process onto `log` — the tail must start when the "Conversation log:" path is detected, not at exit |
| Refresh loses a running analysis; console stuck on `Waiting for events...` | No reconnect endpoint, or the analysis runs inside the SSE generator so the client disconnect killed it | Run the analysis in a background worker, append every event to a durable record (`events.jsonl`), and serve `GET /conversations/{id}/stream` replaying persisted events with `replay: true` then tailing live |
| Conversation stuck on `running` with no process behind it | Run state held only in an in-memory registry, lost on agent restart | On stream attach, reconcile untracked `running` conversations to `interrupted` and emit a terminal event so the client stops waiting |
| Every transformation fails with `ValidationException ... Value at 'resource' failed to satisfy constraint` | Dropdown submitted the display label instead of the CLI identifier, and/or the catalog carried a nested list so entries had no usable value | Submit `atx_definition_name` resolved per source (`id` for AWS-managed, `name` for custom), flatten list-valued definition files, and validate `transformation_type` in the request model so a bad value is a 422 rather than a late CLI failure |
| Transform console stuck on "in progress" after the run ended, or terminating instantly with `status: "running"` | Discriminator put in the SSE `event:` name, which the shared client discards, so the page's `complete`/`error` branch is unreachable; and/or a tail loop keyed on process liveness rather than the record's persisted status | Put `type` in the `data:` payload on every event; key the tail loop on persisted status |
| "Stream replay not available" on running transform | Transform agent missing `/conversations/{id}/stream` endpoint | Rebuild atx-transform-agent container; verify endpoint exists in main.py |
| Console stops updating after initial output | `process.wait()` blocking without draining log_queue | Verify the polling loop with `asyncio.wait_for(process.wait(), timeout=2.0)` is in place |
| Integration diagram alone fails to render (others fine) | Unsanitised Mermaid node IDs built from raw import strings (`import java.util.*` → `java_module --> *`) | Route every identifier through the shared sanitiser; validate diagram source before returning |
| AI documentation says "no codebase provided" while status is `completed` | Prompt template not found in container; silent fallback to a default with no context substituted | Candidate-based path resolution; never report `completed` without substituted context |
| A tab renders a placeholder while the real component exists | Duplicate component file — the routed copy is the placeholder one | Search the component name repo-wide; keep exactly one |
| 422 submitting from an ATX page | Frontend and agent model disagree on the field name (`repository_url` vs `repo_url`) | Check design.md "Agent Request Body Contracts" — the table is the authority, the disagreeing side is the defect |
| ATX analysis fails with "ATX binary not found" while the container reports healthy | CLI install swallowed with `\|\| true` / `\|\| echo`, so the image has no `atx`; the healthcheck only polls HTTP | Remove the swallow so a failed install fails the build; make readiness verify the binary |
| Analysis reaches the CLI with a URL instead of a checkout | No clone step — `atx ... -p` needs a local project directory | Clone remote URLs to local storage first and pass that path |
| Documentation tab empty (or showing JSON metadata) after a completed ATX analysis | Collection looked under the conversation storage dir, while the CLI writes `ATXDocumentation/` into the project path passed to `-p` and mirrors it under its own run dir; and/or the listing carried no content so the tab fell back to dumping metadata | Search the ordered candidate list including `<repo_path>/ATXDocumentation` and the run dir derived from `metadata["conversation_log"]`, copy into `docs/`, serve content via `GET /file`, render as markdown |

---

## Acceptance Coverage Contract (authoritative)

**This document is the single authority on acceptance-test coverage.** Task 30 implements this contract; it does not restate it. If coverage needs to change, change it here — not in `tasks.md`.

### Binding obligations

1. **Every Assertion Rule (Rules 1–23) is binding** on every generated Playwright spec. A spec that satisfies a scenario's prose while violating a Rule is non-compliant. Rules are not style advice.
2. **Every numbered Test in this document is mandatory** unless that Test is explicitly marked optional here. There is no "at minimum" subset, no representative sample, and no deferring a Test because an earlier one passed. Tests 1–15 (including 10a) are all mandatory as written today; no Test is currently marked optional.
3. **Paraphrasing away an assertion is a violation.** Where a scenario names a concrete selector, count, status code, or field name, the generated test asserts on exactly that. Weakening `> 0` to "renders", or `equals "completed"` to "is truthy", is a failed implementation of this contract.
4. **A missing spec file for a route is a failed acceptance run, not a partial pass.** This is Rule 10 stated as an outcome: if a route has no spec exercising it, the acceptance run is FAILED even when every existing spec is green. Absence of coverage is the defect being tested for.
5. **Any test failure blocks build completion.** Fix the defect in the product code. Do not delete the test, skip it, loosen the selector, widen a timeout to mask a hang, or convert a positive assertion into an absence-of-error check.
6. **Container-verified checks cannot be substituted with local checks.** Rules 14 and 15 checks run via `docker compose exec` against the running service — from a spec using `child_process.execSync`, or as the documented shell steps in the owning Test. A local-filesystem equivalent does not discharge the obligation.

### Required spec files and their coverage

These are the spec files under `frontend/e2e/`. Each must exist and must cover the Tests listed. A file that exists but omits one of its Tests is incomplete coverage, which fails the run under obligation 2.

| Spec file | Must cover |
|---|---|
| `navigation.spec.ts` | **Test 1** — all six sidebar routes render real content, active-item highlight, no "under construction" stubs |
| `backend-api.spec.ts` | **Test 2** (health, auth config, analyses list, 404), **Test 7** (transformation definitions CRUD), **Test 9** (`/api/analyze/github` field-name contract, both directions), and the agent field-name contracts of **Tests 5 and 6** (`repository_url` vs `repo_url`, both directions, per Rule 11 — with Rule 13 status-only assertions on the SSE endpoint) |
| `analysis-flow.spec.ts` | **Test 3** (ZIP upload → parse → display), **Test 4** (GitHub clone → analysis → results), **Test 12** (AI-enrichment progress step, completion inside time bounds, UI responsiveness, concurrent analyses) |
| `results-tabs.spec.ts` | **Test 10** (all 8 tabs, no JS errors, real content per tab), **Test 10a** (results route spec — all 8 tabs, `svg circle` > 0, `tbody tr` > 0, every diagram-type variant per Rules 7–8, no placeholder text, no contextless documentation, Upgrades rows reading `name` and `ecosystem` with an advisory-grounded recommendation, an undeterminable version labelled rather than blank, and "no upgrades recommended" distinct from a load failure), **Test 11** (AI enrichment: fresh-analysis `ai_enrichment_status == "completed"`, real technologies named, `skipped` reached only by `SKIP_AI_ENRICHMENT` and `failed` reached by a forced Bedrock timeout per Rule 21, `ai_enrichment_error` naming the operator action, a succeeded stage's output retained and rendered when a later stage fails, and the client's `read_timeout`/retry configuration and first-attempt non-retryable failure verified in-container per Rule 15 alongside prompt resolution) |
| `atx-pages.spec.ts` | **Test 5** and **Test 6** page-driven halves (page loads, SSE console, submit is not 422), Transformation Management source assertions per Rule 18 (AWS Managed tab read from `GET /atx-transform/transformations`, read-only cards, unexecutable entries marked, independent load failures naming the source), `atx --version` in both agent images per Rule 14, **Test 13** (ATX Analysis lifecycle: start/cancel/stream/reconnect/documentation, generated-documentation link following per Rule 20 — containing-document resolution, cross-document fragments, unresolvable links visibly unfollowable, one shared resolution module), **Test 14** (ATX Transform lifecycle: start/stream/history/diff/dropdown, results-page reachability by click per Rule 16, diff payload shape keyed on `filename`/`lines[]`, per-entry `category` and per-category summary counts present at zero per Rule 19, changed-file listbox navigation with full relative paths and keyboard movement, a long line's full content present in the DOM with no horizontal scroll on the diff pane, summary `changed_files`/`additions`/`deletions`, whole-tree chunked download with 413 over cap, record and artefact durability across an agent restart per Rule 17), **Test 15** (ATX error handling and edge cases, including both halves of the missing-configuration scenario per Rule 22 — a definition with no registered default failing by name, and `AWS/java-version-upgrade` clearing CLI startup on the agent's default with the applied default named in the console and recorded as `configuration_source`) |
| `error-handling.spec.ts` | **Test 8** — frontend survives backend down and recovers on restart |

Test 9's snippet refers to `routes.spec.ts`; that coverage lives in `backend-api.spec.ts` above. Either filename satisfies the contract as long as the assertions exist and are discoverable by the route they exercise.

### Completeness self-check before declaring the run green

- Every Test 1–15 (including 10a) maps to at least one spec in the table above, and that spec actually contains it
- Every route the frontend serves has a spec that exercises it (Rule 10)
- Rules 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23 each have at least one executable assertion, not just prose acknowledgement
- Rule 23 specifically: no spec contains `.first()`, `.nth(`, `.last()` or an `if (await …isVisible())` wrapper around an assertion — these are grep-checkable and each is a violation
- Every scenario asserting that an operation **fails** names the requirement making that failure correct (Rule 22); any that cannot is rewritten before the run is declared green
- No assertion was weakened, skipped, or marked `.skip`/`.fixme` to reach green
