import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { test, expect } from '@playwright/test';

test('upload, preview audio, trim, render, and verify rendered audio', async ({ page }) => {
  const browserErrors = [];
  const temporaryDirectory = mkdtempSync(resolve(tmpdir(), 'incubrix-e2e-'));
  page.on('pageerror', error => browserErrors.push(error.message));
  try {
    const musicPath = resolve(temporaryDirectory, 'music.wav');
    execFileSync('ffmpeg', [
      '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
      'sine=frequency=220:sample_rate=48000', '-t', '2', '-c:a', 'pcm_s16le', musicPath,
    ]);
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Video Editor' })).toBeVisible();
    await page.locator('input[type="file"]').nth(0).setInputFiles(resolve(process.cwd(), '..', 'data', 'sample_av.mp4'));
    await expect(page.getByText('AUDIO ON')).toBeVisible();

    const video = page.locator('video');
    await expect.poll(() => video.evaluate(element => element.readyState)).toBeGreaterThanOrEqual(1);
    expect(await video.evaluate(element => ({ muted: element.muted, volume: element.volume }))).toEqual({ muted: false, volume: 1 });
    await page.getByRole('button', { name: 'Play preview' }).click();
    await expect.poll(() => video.evaluate(element => !element.paused)).toBe(true);
    await video.evaluate(element => element.pause());

    await page.locator('input[type="file"]').nth(1).setInputFiles(musicPath);
    await expect(page.getByText('Music added · ducking on')).toBeVisible();
    const endField = page.getByLabel('Keep to');
    const sourceDuration = Number(await endField.inputValue());
    await page.getByLabel('Keep from').fill('0.1');
    await endField.fill(String(sourceDuration - 0.1));
    await page.getByRole('button', { name: 'Apply trim' }).click();
    await expect(page.getByText(/1 clip/)).toBeVisible();

    await page.getByLabel('Describe an edit').fill('bleep from 0.2 seconds to 0.4 seconds');
    await page.getByRole('button', { name: 'Send instruction' }).click();
    await expect(page.getByText('BLEEPS')).toBeVisible();
    await page.getByRole('button', { name: /Render video/ }).click();
    const download = page.getByRole('link', { name: /Download rendered MP4/ });
    await expect(download).toBeVisible({ timeout: 4 * 60 * 1000 });
    await expect.poll(() => video.evaluate(element => element.readyState)).toBeGreaterThanOrEqual(1);
    expect(await video.evaluate(element => ({ muted: element.muted, volume: element.volume }))).toEqual({ muted: false, volume: 1 });
    await page.getByRole('button', { name: 'Play preview' }).click();
    await expect.poll(() => video.evaluate(element => !element.paused)).toBe(true);
    await video.evaluate(element => element.pause());
    const response = await page.request.get(await download.getAttribute('href'));
    expect(response.ok()).toBe(true);

    const renderedPath = resolve(temporaryDirectory, 'rendered.mp4');
    writeFileSync(renderedPath, await response.body());
    const metadata = JSON.parse(execFileSync('ffprobe', [
      '-v', 'error', '-show_streams', '-show_format', '-of', 'json', renderedPath,
    ], { encoding: 'utf8' }));
    expect(metadata.streams.some(stream => stream.codec_type === 'audio')).toBe(true);
    expect(browserErrors).toEqual([]);
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
});

test('editor remains usable at a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Video Editor' })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport);
  await expect(page.getByRole('button', { name: /Upload video/ }).first()).toBeVisible();
});
