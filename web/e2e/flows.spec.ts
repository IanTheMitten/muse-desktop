import { expect, test, type Page } from "@playwright/test";
import { open } from "./app";

const playerTitle = (page: Page) => page.locator(".player-title");
const songs = (page: Page) => page.getByRole("button", { name: "Songs" });
const sheet = (page: Page) => page.getByRole("dialog", { name: "Now playing" });

test.describe.serial("flows", () => {
  test("play, next and the queue", async ({ page }) => {
    await open(page, "/library");
    await songs(page).click();
    await page.getByRole("button", { name: /First Light/ }).first().click();
    await expect(playerTitle(page)).toHaveText("First Light");
    await page.getByRole("button", { name: "Next", exact: true }).first().click();
    await expect(playerTitle(page)).not.toHaveText("First Light");
    await page.getByRole("button", { name: /^Open now playing: / }).click();
    await sheet(page).getByRole("button", { name: "Up next" }).first().click();
    await expect(sheet(page).getByText("Song 05")).toBeVisible();
  });

  test("radio follows the arc", async ({ page, request }) => {
    // The page restores the last queue and counts it as heard; the Songs-tab play above queued all 12.
    await request.put("/api/player/state?t=e2e-token", { data: { queue: [1000000011], client_state: "playing" } });
    await open(page, "/library");
    await page.getByRole("button", { name: "Albums" }).click();
    await page.getByRole("link", { name: /Album 00/ }).first().click();
    await page.getByRole("button", { name: /First Light/ }).first().click();
    await page.getByRole("button", { name: "Pause", exact: true }).first().click({ timeout: 1500 }).catch(() => {}); // fixture tracks last 1s; may already be over
    await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Muse" }).click();
    await expect(page.getByText(/ · Muse$/).first()).toBeVisible();
    await expect(page.getByText("This song has no lyric tags, so Radio follows its sound.")).toBeVisible();
    const lift = page.getByRole("switch", { name: "Lift" });
    await expect(lift).toHaveAttribute("aria-checked", "true");
    await lift.click();
    await expect(lift).toHaveAttribute("aria-checked", "false");
    // Leave the saved player where the next test expects a fresh page to find it.
    await request.put("/api/player/state?t=e2e-token", { data: { queue: [1000000011], client_state: "playing" } });
  });

  test("missing lyrics", async ({ page }) => {
    await open(page, "/");
    await expect(page.getByText("2 songs have no lyrics")).toBeVisible();
    await page.getByRole("link", { name: "Add lyrics" }).click();
    await expect(page).toHaveURL(/\/library\/missing-lyrics/);
    await page.getByRole("button", { name: "Song has no words: Song 02" }).click();
    await expect(page.locator("p.vh[role=status]")).toHaveText("Marked Song 02 as having no words.");
    await expect(page.getByText("1 song", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Add lyrics" }).click();
    await page.getByRole("textbox", { name: "Lyrics" }).fill("la la");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Every song has lyrics or is marked as having none.")).toBeVisible();
  });

  test("edit details survives a reload", async ({ page }) => {
    await open(page, "/library");
    await songs(page).click();
    await page.getByRole("button", { name: "More for Song 03" }).click();
    await page.getByRole("menuitem", { name: "Edit details" }).click();
    await page.getByLabel("Title").fill("E2E Renamed");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.reload();
    await page.getByRole("navigation", { name: "Sections" }).waitFor();
    await songs(page).click();
    await expect(page.getByText("E2E Renamed").first()).toBeVisible();
  });

  test("remove then restore", async ({ page }) => {
    await open(page, "/library");
    await songs(page).click();
    await page.getByRole("button", { name: "More for Song 04" }).click();
    await page.getByRole("menuitem", { name: "Remove from library…" }).click();
    await page.getByRole("button", { name: "Remove from library", exact: true }).click();
    await expect(page.getByRole("button", { name: "More for Song 04" })).toHaveCount(0);
    await open(page, "/settings");
    await page.getByRole("button", { name: "Restore Song 04" }).click();
    await open(page, "/library");
    await songs(page).click();
    await expect(page.getByRole("button", { name: "More for Song 04" })).toBeVisible();
  });

  test("lyrics and tap along keys", async ({ page, request }) => {
    await open(page, "/library");
    await songs(page).click();
    await page.getByRole("button", { name: /First Light/ }).first().click();
    await page.getByRole("button", { name: "Next", exact: true }).first().click();
    await page.getByRole("button", { name: "Pause", exact: true }).first().click(); // fixture tracks last 1s
    await expect(playerTitle(page)).not.toHaveText("First Light");
    const title = (await playerTitle(page).textContent())!;
    const list = await (await request.get("/api/library/songs?t=e2e-token")).json();
    const rows = Array.isArray(list) ? list : list.results;
    const rowid = rows.find((t: { track_name: string }) => t.track_name === title).track_rowid;
    const text = ["one", "two", "three", "four", "five"].join("\n");
    const put = await request.put(`/api/tracks/${rowid}/lyrics?t=e2e-token`, { data: { text } });
    expect(put.ok()).toBe(true);
    await page.getByRole("button", { name: /^Open now playing: / }).click();
    await sheet(page).getByRole("button", { name: "Lyrics" }).first().click();
    await sheet(page).getByRole("button", { name: "Tap along" }).click();
    await page.keyboard.press("Space");
    await expect(page.getByText("Line 2 of 5")).toBeVisible();
    await page.keyboard.press("Backspace");
    await expect(page.getByText("Line 1 of 5")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByText("Line 1 of 5")).toHaveCount(0);
    await expect(sheet(page)).toHaveClass(/\bup\b/);
  });

  test("settings sections", async ({ page }) => {
    await open(page, "/settings");
    for (const name of ["Music folders", "Playback", "Duplicates", "Hidden artists", "Removed songs", "Advanced"]) {
      await expect(page.getByRole("heading", { name })).toBeVisible();
    }
  });
});
