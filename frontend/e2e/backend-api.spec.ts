import { test, expect, type Page } from '@playwright/test';

/**
 * Test 2: Backend Health & Core API
 * Test 7: Transformation Definitions CRUD
 * Test 9: GitHub Analysis API Contract
 */
test.describe('Backend Health & Core API (Test 2)', () => {
  test('GET /health returns 200 with healthy status', async ({ request }) => {
    const response = await request.get('http://localhost:8000/health');
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.status).toBe('healthy');
  });

  test('GET /api/auth/config returns 200 with mode field', async ({ request }) => {
    const response = await request.get('http://localhost:8000/api/auth/config');
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toHaveProperty('mode');
  });

  test('GET /api/analyses returns 200 with analyses array', async ({ request }) => {
    const response = await request.get('http://localhost:8000/api/analyses');
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toHaveProperty('analyses');
    expect(Array.isArray(body.analyses)).toBe(true);
  });

  test('GET /api/analysis/nonexistent/status returns 404', async ({ request }) => {
    const response = await request.get('http://localhost:8000/api/analysis/nonexistent/status');
    expect(response.status()).toBe(404);
  });
});

test.describe('Transformation Definitions CRUD (Test 7)', () => {
  const testDefinition = {
    name: 'e2e-test-transform',
    description: 'E2E test transformation definition',
    source_language: 'Java',
    target_language: 'Java',
    transformation_type: 'modernization',
    rules: []
  };
  let createdId: string | undefined;

  test('POST create transformation definition', async ({ request }) => {
    const response = await request.post('http://localhost:8000/api/transformations/definitions', {
      data: testDefinition
    });
    // Accept 200 or 201
    expect([200, 201]).toContain(response.status());
    const body = await response.json();
    // Store the ID for cleanup
    if (body.id) {
      createdId = body.id;
    } else if (body.definition && body.definition.id) {
      createdId = body.definition.id;
    }
  });

  test('GET list includes created definition', async ({ request }) => {
    // First create one to ensure it exists
    const createResp = await request.post('http://localhost:8000/api/transformations/definitions', {
      data: { ...testDefinition, name: 'e2e-list-test' }
    });
    expect([200, 201]).toContain(createResp.status());
    const created = await createResp.json();
    const defId = created.id || created.definition?.id;

    // Now list
    const response = await request.get('http://localhost:8000/api/transformations/definitions');
    expect(response.status()).toBe(200);
    const body = await response.json();
    const definitions = body.definitions || body;
    expect(Array.isArray(definitions)).toBe(true);
    
    // Verify our created definition is in the list
    const found = definitions.some((d: { name?: string; id?: string }) => 
      d.name === 'e2e-list-test' || d.id === defId
    );
    expect(found).toBe(true);

    // Cleanup
    if (defId) {
      await request.delete(`http://localhost:8000/api/transformations/definitions/${defId}`);
    }
  });

  test('DELETE removes transformation definition', async ({ request }) => {
    // Create one
    const createResp = await request.post('http://localhost:8000/api/transformations/definitions', {
      data: { ...testDefinition, name: 'e2e-delete-test' }
    });
    expect([200, 201]).toContain(createResp.status());
    const created = await createResp.json();
    const defId = created.id || created.definition?.id;
    expect(defId).toBeTruthy();

    // Delete it
    const deleteResp = await request.delete(`http://localhost:8000/api/transformations/definitions/${defId}`);
    expect([200, 204]).toContain(deleteResp.status());

    // Verify it's gone
    const listResp = await request.get('http://localhost:8000/api/transformations/definitions');
    const body = await listResp.json();
    const definitions = body.definitions || body;
    const found = definitions.some((d: { id?: string }) => d.id === defId);
    expect(found).toBe(false);
  });
});

/**
 * Agent request-body field-name contracts.
 *
 * Build Constraint 8: ATX Analysis uses `repository_url`, ATX Transform uses
 * `repo_url`. A 422 means the frontend and the agent's Pydantic model disagree.
 * Both directions are asserted: the correct name must NOT 422, and the wrong
 * name MUST 422 — otherwise a future rename would pass silently.
 *
 * Requests are issued from the page context with fetch + AbortController (the
 * same pattern the frontend uses) so that SSE endpoints can be asserted on
 * their status and then aborted, rather than consumed to completion.
 */
async function postJsonStatus(page: Page, url: string, body: unknown): Promise<number> {
  return page.evaluate(
    async ({ url, body }) => {
      const controller = new AbortController();
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        return response.status;
      } finally {
        // Close the stream without reading it so the test cannot hang.
        controller.abort();
      }
    },
    { url, body }
  );
}

test.describe('Agent Request Contract — field names', () => {
  test.beforeEach(async ({ page }) => {
    // Same-origin page context so /atx/ and /atx-transform/ resolve via nginx.
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
  });

  test('POST /atx/analyze with repository_url does not return 422', async ({ page }) => {
    const status = await postJsonStatus(page, '/atx/analyze', {
      repository_url: 'https://github.com/octocat/Hello-World',
      branch: 'main',
      analysis_type: 'code-assessment',
    });
    expect(status).not.toBe(422);
  });

  test('POST /atx/analyze with repo_url returns 422', async ({ page }) => {
    const status = await postJsonStatus(page, '/atx/analyze', {
      repo_url: 'https://github.com/octocat/Hello-World',
      analysis_type: 'code-assessment',
    });
    expect(status).toBe(422);
  });

  test('POST /atx-transform/transform with repo_url does not return 422', async ({ page }) => {
    const status = await postJsonStatus(page, '/atx-transform/transform', {
      repo_url: 'https://github.com/octocat/Hello-World',
      branch: 'main',
      transformation_type: 'AWS/java-upgrade',
    });
    expect(status).not.toBe(422);
  });

  test('POST /atx-transform/transform with repository_url returns 422', async ({ page }) => {
    const status = await postJsonStatus(page, '/atx-transform/transform', {
      repository_url: 'https://github.com/octocat/Hello-World',
      branch: 'main',
      transformation_type: 'AWS/java-upgrade',
    });
    expect(status).toBe(422);
  });
});

test.describe('GitHub Analysis API Contract (Test 9)', () => {
  test('POST /api/analyze/github with correct field names does not return 422', async ({ request }) => {
    const response = await request.post('http://localhost:8000/api/analyze/github', {
      data: {
        repo_url: 'https://github.com/octocat/Hello-World'
      }
    });
    // Should not be a 422 validation error - may be 200, 202, or other status
    // but never 422 (which indicates field name mismatch)
    expect(response.status()).not.toBe(422);
  });

  test('POST /api/analyze/github with wrong field names returns 422', async ({ request }) => {
    const response = await request.post('http://localhost:8000/api/analyze/github', {
      data: {
        wrong_field_name: 'https://github.com/octocat/Hello-World'
      }
    });
    // Should be 422 because field names don't match the Pydantic model
    expect(response.status()).toBe(422);
  });
});
