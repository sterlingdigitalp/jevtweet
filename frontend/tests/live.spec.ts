/** Explicit opt-in: real Jev UI integration, private artifacts, no predictive claims. */
import { test, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
test("live UI judgment is persisted with pinned model and an inspectable rating", async ({
  page,
}) => {
  test.skip(
    process.env.JEVTWEET_E2E_LIVE !== "1" || !process.env.JEVTWEET_E2E_URL,
    "Requires a local live-configured server, explicit $ ceiling and JEVTWEET_E2E_LIVE=1.",
  );
  await page.goto("/");
  await page
    .getByRole("combobox", { name: "Execution", exact: true })
    .selectOption("live");
  await page
    .getByRole("textbox", { name: "Candidate", exact: true })
    .fill(
      "Our AI-written migration passed review but dropped an index. We now replay migrations on a production-shaped fixture and compare query plans before every merge.",
    );
  const pending = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/judge" &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Judge candidate", exact: true })
    .click();
  const response = await pending;
  expect(response.ok()).toBeTruthy();
  const result = await response.json();
  expect(result.execution_mode).toBe("live");
  expect(result.model_requested).toBe("jev-1.13.0");
  expect(result.model_returned).toBe("jev-1.13.0");
  expect(result.status).toBe("scored");
  expect(result.score_1_to_5).toBeGreaterThanOrEqual(1);
  expect(result.score_1_to_5).toBeLessThanOrEqual(5);
  expect(result.breakout_probability).toBeNull();
  await expect(page.getByText("LIVE · Jev", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Inspect run", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Run inspector" }),
  ).toBeVisible();
  const directory =
    process.env.JEVTWEET_UI_ARTIFACT_DIR || "../private/live-ui";
  await mkdir(directory, { recursive: true });
  await writeFile(`${directory}/result.json`, JSON.stringify(result, null, 2), {
    mode: 0o600,
  });
  await page.screenshot({ path: `${directory}/screen.png`, fullPage: true });
});
