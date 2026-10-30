import type { Page } from "@playwright/test";

export async function open(page: Page, path = "/") {
  await page.goto(path + "?t=e2e-token");
  await page.getByRole("navigation", { name: "Sections" }).waitFor();
}
