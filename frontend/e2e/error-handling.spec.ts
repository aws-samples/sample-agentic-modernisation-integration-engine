import { test, expect } from '@playwright/test';

/**
 * Test 8: Frontend Error Handling
 * Verify pages render gracefully even when backend returns errors.
 * The page should show form UI unconditionally.
 */
test.describe('Frontend Error Handling (Test 8)', () => {
  test('ATX Analysis page renders form even if API call fails', async ({ page }) => {
    // Mock the ATX Analysis conversations endpoint to fail
    await page.route('**/atx-analysis/conversations', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' })
      });
    });

    await page.goto('/atx-analysis');
    await page.waitForLoadState('networkidle');
    
    // The form UI should still render despite API error
    const input = page.locator('input[type="text"], input[type="url"], textarea').first();
    await expect(input).toBeVisible({ timeout: 10000 });
    
    // The page should NOT show a crash or blank screen
    const body = await page.locator('body').textContent();
    expect(body?.length).toBeGreaterThan(0);
    expect(body).not.toContain('Cannot read properties');
    expect(body).not.toContain('TypeError');
  });

  test('ATX Transform page renders form even if API call fails', async ({ page }) => {
    // Mock the ATX Transform endpoints to fail
    await page.route('**/atx-transform/**', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' })
      });
    });

    // Navigate via SPA routing to avoid nginx proxy conflict
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    
    // Expand "AWS Transform" section
    const sectionHeader = page.locator('text=AWS Transform').first();
    if (await sectionHeader.isVisible()) {
      await sectionHeader.click();
      await page.waitForTimeout(300);
    }
    
    // Click ATX Transform nav item
    const navItem = page.locator('text=ATX Transform').first();
    if (await navItem.isVisible()) {
      await navItem.click();
      await page.waitForTimeout(2000);
    }
    
    // The page should NOT crash
    const body = await page.locator('body').textContent();
    expect(body?.length).toBeGreaterThan(0);
    expect(body).not.toContain('Cannot read properties');
    expect(body).not.toContain('TypeError');
  });

  test('Transformations page renders even if definitions API fails', async ({ page }) => {
    // Mock the transformations definitions endpoint to fail
    await page.route('**/api/transformations/definitions', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' })
      });
    });

    await page.goto('/transformations');
    await page.waitForLoadState('networkidle');
    
    // Page should render gracefully
    const body = await page.locator('body').textContent();
    expect(body?.length).toBeGreaterThan(0);
    expect(body).not.toContain('Cannot read properties');
    expect(body).not.toContain('TypeError');
  });

  test('Dashboard renders even if analyses API fails', async ({ page }) => {
    // Mock the analyses endpoint to fail
    await page.route('**/api/analyses', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' })
      });
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');
    
    // Dashboard should still render its content
    const dashboardText = page.locator('text=Dashboard').first();
    await expect(dashboardText).toBeVisible({ timeout: 10000 });
  });

  test('Previous Analyses page renders even if API fails', async ({ page }) => {
    // Mock the analyses endpoint to fail
    await page.route('**/api/analyses', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' })
      });
    });

    await page.goto('/previous');
    await page.waitForLoadState('networkidle');
    
    // Page should render gracefully (showing empty state or error message, not crash)
    const body = await page.locator('body').textContent();
    expect(body?.length).toBeGreaterThan(0);
    expect(body).not.toContain('Cannot read properties');
    expect(body).not.toContain('TypeError');
  });
});
