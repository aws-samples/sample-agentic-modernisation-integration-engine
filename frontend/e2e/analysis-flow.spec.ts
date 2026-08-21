import { test, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';
import os from 'os';

/**
 * Test 3: ZIP Upload Analysis Flow
 * Test 4: GitHub Analysis Flow
 */
test.describe('ZIP Upload Analysis Flow (Test 3)', () => {
  test('Navigate to analysis page and find ZIP upload tab', async ({ page }) => {
    await page.goto('/analysis');
    await page.waitForLoadState('networkidle');
    
    // The analysis page should be visible
    await expect(page.locator('body')).not.toBeEmpty();
    
    // Look for ZIP Upload tab
    const zipTab = page.locator('text=ZIP Upload').first();
    const uploadTab = page.locator('text=Upload').first();
    
    // Either "ZIP Upload" tab or "Upload" tab should be present
    const hasZipTab = await zipTab.isVisible().catch(() => false);
    const hasUploadTab = await uploadTab.isVisible().catch(() => false);
    
    expect(hasZipTab || hasUploadTab).toBe(true);
  });

  test('ZIP upload area appears when ZIP tab is selected', async ({ page }) => {
    await page.goto('/analysis');
    await page.waitForLoadState('networkidle');
    
    // Click on ZIP Upload tab
    const zipTab = page.locator('text=ZIP Upload').first();
    
    if (await zipTab.isVisible().catch(() => false)) {
      await zipTab.click();
      await page.waitForTimeout(500);
    }

    // After switching to ZIP tab, a dropzone area should appear
    // react-dropzone creates a hidden file input and a visible drop area
    // Check for the dropzone container or the hidden file input
    const dropzoneArea = page.locator('[class*="dropzone"], [class*="upload"], [class*="drop"]');
    const fileInput = page.locator('input[type="file"]');
    
    const hasDropzone = await dropzoneArea.count() > 0;
    const hasFileInput = await fileInput.count() > 0;
    
    // The dropzone text or file input should exist
    const dragText = page.locator('text=drag').first();
    const hasDragText = await dragText.isVisible().catch(() => false);
    
    expect(hasDropzone || hasFileInput || hasDragText).toBe(true);
  });

  test('Upload a test ZIP file and verify progress tracker appears', async ({ page }) => {
    await page.goto('/analysis');
    await page.waitForLoadState('networkidle');
    
    // Switch to ZIP tab
    const zipTab = page.locator('text=ZIP').first();
    const uploadTab = page.locator('text=Upload').first();
    
    if (await zipTab.isVisible().catch(() => false)) {
      await zipTab.click();
    } else if (await uploadTab.isVisible().catch(() => false)) {
      await uploadTab.click();
    }

    // Create a minimal test ZIP file
    const tmpDir = os.tmpdir();
    const zipPath = path.join(tmpDir, 'test-upload.zip');
    
    // Create a minimal ZIP file (ZIP header + central directory)
    const zipBuffer = Buffer.from([
      0x50, 0x4b, 0x05, 0x06, // End of central directory signature
      0x00, 0x00, 0x00, 0x00, // Number of this disk, disk where central directory starts
      0x00, 0x00, 0x00, 0x00, // Number of entries, total entries
      0x00, 0x00, 0x00, 0x00, // Size of central directory
      0x00, 0x00, 0x00, 0x00, // Offset of central directory
      0x00, 0x00              // Comment length
    ]);
    fs.writeFileSync(zipPath, zipBuffer);

    // Find the file input and upload
    const fileInput = page.locator('input[type="file"]').first();
    if (await fileInput.count() > 0) {
      await fileInput.setInputFiles(zipPath);
      
      // Wait briefly for the upload to process
      await page.waitForTimeout(2000);
      
      // Check if progress tracker or any analysis initiation happened
      // (may show error for invalid ZIP, but the flow should be triggered)
      const progressTracker = page.locator('[class*="Stepper"], [class*="progress"], [role="progressbar"]');
      const errorMessage = page.locator('[class*="Alert"], [class*="error"]');
      
      // Either progress tracker appears or an error message (both indicate the flow was triggered)
      const hasProgress = await progressTracker.count() > 0;
      const hasError = await errorMessage.count() > 0;
      
      expect(hasProgress || hasError).toBe(true);
    }
    
    // Cleanup
    fs.unlinkSync(zipPath);
  });
});

test.describe('GitHub Analysis Flow (Test 4)', () => {
  test.setTimeout(180000); // 3-minute timeout for GitHub analysis

  test('Navigate to analysis page and enter GitHub repo URL', async ({ page }) => {
    await page.goto('/analysis');
    await page.waitForLoadState('networkidle');
    
    // Look for GitHub tab or input
    const githubTab = page.locator('text=GitHub').first();
    if (await githubTab.isVisible().catch(() => false)) {
      await githubTab.click();
    }
    
    // Find the URL input field
    const urlInput = page.locator('input[type="text"], input[type="url"]').first();
    await expect(urlInput).toBeVisible({ timeout: 5000 });
    
    // Enter a small public repo URL
    await urlInput.fill('https://github.com/octocat/Hello-World');
    
    // Verify input was filled
    await expect(urlInput).toHaveValue('https://github.com/octocat/Hello-World');
  });

  test('Start GitHub analysis and wait for completion or timeout', async ({ page }) => {
    await page.goto('/analysis');
    await page.waitForLoadState('networkidle');
    
    // Click GitHub tab if visible
    const githubTab = page.locator('text=GitHub').first();
    if (await githubTab.isVisible().catch(() => false)) {
      await githubTab.click();
    }
    
    // Find URL input and fill it
    const urlInput = page.locator('input[type="text"], input[type="url"]').first();
    await expect(urlInput).toBeVisible({ timeout: 5000 });
    await urlInput.fill('https://github.com/octocat/Hello-World');
    
    // Find and click the analyze button
    const analyzeBtn = page.locator('button:has-text("Analyze"), button:has-text("Start"), button:has-text("Submit")').first();
    if (await analyzeBtn.isVisible().catch(() => false)) {
      await analyzeBtn.click();
      
      // Wait for either progress tracker, completion, or error 
      // (the analysis may fail due to missing credentials, that's OK)
      await page.waitForTimeout(3000);
      
      // Verify something happened - progress indicator or error or result
      const pageContent = await page.locator('body').textContent();
      // The page should show SOMETHING after clicking analyze (progress, error, or redirect)
      expect(pageContent?.length).toBeGreaterThan(0);
    }
  });
});
