import { expect, test } from "@playwright/test";
import { open } from "./app";

test.describe.serial("unlock", () => {
  test("locked, then unlocked, then locked again", async ({ page }) => {
    const nav = page.getByRole("navigation", { name: "Sections" });
    await open(page, "/");
    const chart = page.locator("section", { has: page.getByRole("heading", { name: "Top songs right now" }) });
    await expect(chart.getByText("First Light")).toBeVisible();
    await expect(chart.getByText("Not Owned Song")).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Add" })).toHaveCount(0);
    await open(page, "/add");
    await expect(page).toHaveURL(/\/(\?|$)/);

    await open(page, "/settings");
    await expect(page.getByText("Plugins are under development.")).toBeVisible();
    await page.keyboard.press("Control+Shift+Alt+KeyD");
    const dialog = page.getByRole("dialog", { name: "Administrator password" });
    await dialog.getByLabel("Password").fill("nope");
    await dialog.getByRole("button", { name: "Unlock" }).click();
    await expect(dialog.getByText("That's not it.")).toBeVisible();
    await dialog.getByLabel("Password").fill("download-enjoy");
    await dialog.getByRole("button", { name: "Unlock" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Add" })).toBeVisible();

    await nav.getByRole("link", { name: "Add" }).click();
    await expect(page.getByText("Couldn't get the downloader.")).toBeVisible();
    await nav.getByRole("link", { name: "Home" }).click();
    await expect(page.getByText("Not Owned Song")).toBeVisible();

    await open(page, "/settings");
    await page.getByRole("button", { name: "Lock again" }).click();
    await expect(nav.getByRole("link", { name: "Add" })).toHaveCount(0);
  });
});
