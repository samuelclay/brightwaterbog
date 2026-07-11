import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("renders the trail, advances galleries, and opens the image viewer", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Bright Water Bog");
  await expect(page.getByRole("heading", { name: "Bright Water Bog" })).toBeVisible();

  await page.locator("#stargate").scrollIntoViewIfNeeded();
  await expect(page.locator("#stargate").getByRole("heading", { name: "Stargate" })).toBeVisible();
  await expect(page.locator("#stargate [data-gallery-track]")).toHaveAttribute("data-lightbox-ready", "true");
  const thenPill = page.locator("#stargate [data-gallery-jump='then']");
  await expect(thenPill).toBeVisible();
  await thenPill.click();
  await expect.poll(() => page.locator("#stargate [data-gallery-track]").evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);

  await page.locator("#stargate a[data-pswp]").first().click();
  await expect(page.locator(".pswp")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".pswp")).toBeHidden();
});

test("shows calculated 24-hour visit times", async ({ page }) => {
  await page.goto("/#visit");
  await page.locator("#visit").scrollIntoViewIfNeeded();
  await expect(page.locator("[data-opens-time]")).toHaveText(/^\d{2}:\d{2}$/);
  await expect(page.locator("[data-closes-time]")).toHaveText(/^\d{2}:\d{2}$/);
});

test("opens a complete indoor archive from the compact gallery grid", async ({ page }) => {
  await page.goto("/#indoor");
  const piece = page.locator("#unfinished-shed-dancers");
  await piece.scrollIntoViewIfNeeded();
  await expect(piece.locator("[data-gallery-track]")).toHaveAttribute("data-lightbox-ready", "true");
  await expect(piece.locator("a[data-pswp]")).toHaveCount(10);
  await piece.locator(".indoor-piece__image:not(.indoor-piece__image--source)").click();
  await expect(page.locator(".pswp")).toBeVisible();
  await expect(page.locator(".pswp__counter")).toContainText("10");
  await page.keyboard.press("Escape");
  await expect(page.locator(".pswp")).toBeHidden();
});

test("opens the compact trail map on mobile", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile-only map behavior");
  await page.goto("/");
  await page.locator("#trail").scrollIntoViewIfNeeded();
  await page.locator("[data-open-map]").click();
  await expect(page.locator("[data-map-dialog]")).toBeVisible();
  await page.locator("[data-close-map]").click();
  await expect(page.locator("[data-map-dialog]")).toBeHidden();
});

test("has no automatically detectable accessibility violations in the trail shell", async ({ page }) => {
  await page.goto("/");
  await page.locator("#trail").scrollIntoViewIfNeeded();
  const results = await new AxeBuilder({ page }).exclude(".pswp").analyze();
  expect(results.violations).toEqual([]);
});
