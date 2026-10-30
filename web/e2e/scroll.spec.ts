import { expect, test } from "@playwright/test";
import { open } from "./app";

test.use({ viewport: { width: 1200, height: 420 } });

const overflows = (page: import("@playwright/test").Page) =>
  page.evaluate(() => document.documentElement.scrollHeight > innerHeight);

async function expectScrolls(page: import("@playwright/test").Page) {
  await page.mouse.move(600, 200);
  await page.mouse.wheel(0, 600);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
}

test("every main view scrolls", async ({ page }) => {
  for (const path of ["/library", "/settings"]) {
    await open(page, path);
    await expect.poll(() => overflows(page)).toBe(true);
  }
  for (const path of ["/", "/library", "/settings", "/muse"]) {
    await open(page, path);
    await page.waitForTimeout(500);
    if (await overflows(page)) await expectScrolls(page);
  }
});

test("still scrolls after now playing opens and closes", async ({ page }) => {
  await open(page, "/library");
  await page.getByRole("button", { name: "Songs" }).click();
  await page.getByRole("button", { name: /First Light/ }).first().click();
  await page.getByRole("button", { name: /^Open now playing: / }).click();
  await page.getByRole("button", { name: "Close now playing" }).click();
  await expect(page.getByRole("button", { name: "Close now playing" })).toHaveCount(0);
  await expect.poll(() => overflows(page)).toBe(true);
  await expectScrolls(page);
});
