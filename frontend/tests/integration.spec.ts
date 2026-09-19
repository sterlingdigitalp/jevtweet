/** Opt-in browser tests against the real local backend. Every judgment uses mock execution.
 * Run only against a disposable/private local dataset: JEVTWEET_E2E_URL=http://127.0.0.1:8001 npm run test:integration.
 * No API route interception, live provider calls, real outcomes, or accuracy claims.
 */
import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
const enabled = Boolean(process.env.JEVTWEET_E2E_URL);
const runId = Date.now().toString(36);
const localDate = (iso: string) => {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
const responseFor = (page: Page, path: string) =>
  page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === path &&
      response.request().method() === "POST",
  );
// All tests share one disposable backend. Freeze the generated fixture before imports
// create corpus candidates; serial execution makes this prerequisite deterministic.
test.describe.configure({ mode: "serial" });
test.describe("real backend / staged evaluation workflow", () => {
  test.skip(!enabled, "Requires a disposable backend with the built frontend.");
  test.setTimeout(120_000);
  test("preflight and readiness precede immutable freeze and explicit final holdout opening", async ({
    page,
  }) => {
    await page.goto("/#experiments");
    await page
      .getByRole("combobox", { name: "Dataset", exact: true })
      .selectOption("synthetic");
    await page
      .getByRole("textbox", { name: "Comparison population", exact: true })
      .fill("Original synthetic workflow fixture only");
    await page
      .getByRole("textbox", { name: "Sampling declaration", exact: true })
      .fill(
        "Generated synthetic mechanics data; this is not representative predictive research.",
      );
    await page
      .getByLabel("Representative sampling declared", { exact: true })
      .check();
    const readinessPending = responseFor(page, "/api/experiments/readiness");
    await page
      .getByRole("button", {
        name: "Estimate collection readiness",
        exact: true,
      })
      .click();
    const readinessResponse = await readinessPending;
    expect(readinessResponse.ok()).toBe(true);
    const readiness = await readinessResponse.json();
    expect(readiness.scope).toBe("development_only");
    expect(readiness.test_outcomes_inspected).toBe(false);
    const preflightPending = responseFor(page, "/api/experiments/preflight");
    await page
      .getByRole("button", { name: "Check preflight", exact: true })
      .click();
    const preflightResponse = await preflightPending;
    expect(preflightResponse.ok()).toBe(true);
    const preflight = await preflightResponse.json();
    expect(preflight.ready_to_freeze).toBe(true);
    expect(preflight.test_exposed).toBe(false);
    const freezePending = responseFor(page, "/api/experiments/freeze");
    await page
      .getByRole("button", { name: "Freeze candidate", exact: true })
      .click();
    const freezeResponse = await freezePending;
    expect(freezeResponse.ok()).toBe(true);
    const frozen = await freezeResponse.json();
    expect(frozen.status).toBe("frozen");
    expect(frozen.test_exposed).toBe(false);
    expect(frozen.metrics).toEqual({});
    await expect(
      page.getByRole("textbox", { name: "Sampling declaration", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Frozen declarations", { exact: true }),
    ).toBeVisible();
    const openPending = responseFor(
      page,
      `/api/experiments/${frozen.experiment_id}/open-holdout`,
    );
    await page
      .getByRole("button", { name: "Open final holdout once", exact: true })
      .click();
    const openResponse = await openPending;
    expect(openResponse.ok()).toBe(true);
    const evaluated = await openResponse.json();
    expect(evaluated.status).toBe("evaluated");
    expect(evaluated.test_exposed).toBe(true);
    expect(evaluated.frozen_candidate_hash).toBe(frozen.frozen_candidate_hash);
    expect(evaluated.forecast_available).toBe(false);
    expect(evaluated.predictive_validation).toBe("not_established");
    await expect(
      page.getByRole("button", {
        name: "Open final holdout once",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      page.getByText("Same-cohort method comparison", { exact: true }),
    ).toBeVisible();
  });
});

test.describe("real backend / explicitly mock provider", () => {
  test.skip(
    !enabled,
    "Set JEVTWEET_E2E_URL to a disposable local backend with built frontend.",
  );
  test.describe.configure({ mode: "serial", timeout: 120_000 });
  test.use({ actionTimeout: 15_000 });
  test("paste persists an inspectable judgment and compares independent variants", async ({
    page,
  }) => {
    await page.goto("/");
    await page
      .getByRole("combobox", { name: "Execution", exact: true })
      .selectOption("mock");
    await page
      .getByRole("textbox", { name: "Candidate", exact: true })
      .fill(
        `Synthetic browser fixture ${runId}. Our release checklist now includes a failing migration test, a rollback rehearsal, and a recorded review of the generated patch.`,
      );
    const pending = responseFor(page, "/api/judge");
    await page.getByRole("button", { name: "Judge candidate" }).click();
    const response = await pending;
    expect(response.ok()).toBe(true);
    const judgment = await response.json();
    expect(judgment.execution_mode).toBe("mock");
    expect(["scored", "partial", "abstained"]).toContain(judgment.status);
    expect(judgment.breakout_probability).toBeNull();
    await expect(
      page.getByText("MOCK · synthetic answers", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Forecast unavailable · not promoted" }),
    ).toBeDisabled();
    const inspected = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname ===
        `/api/judgments/${judgment.judgment_id}`,
    );
    await page.getByRole("button", { name: "Inspect run" }).click();
    const record = await (await inspected).json();
    expect(record.judgment.judgment_id).toBe(judgment.judgment_id);
    expect(record.state).toBeTruthy();
    expect(record.questions).toBeTruthy();
    await page.getByText("State sent to Jev", { exact: true }).click();
    await expect(
      page
        .locator(".inspector details[open] pre")
        .filter({ hasText: `Synthetic browser fixture ${runId}` }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Compare variants →" }).click();
    await page
      .getByRole("textbox", { name: "Candidate A", exact: true })
      .fill(
        `Synthetic variant A ${runId}: We used a reproduction test to catch a mistaken assumption before accepting an agent patch.`,
      );
    await page
      .getByRole("textbox", { name: "Candidate B", exact: true })
      .fill(
        `Synthetic variant B ${runId}: Show the reproduction test with an agent patch so reviewers can verify the assumption.`,
      );
    const compared = responseFor(page, "/api/compare");
    await page
      .getByRole("button", { name: "Compare variants", exact: true })
      .click();
    const comparison = await (await compared).json();
    expect(comparison.results).toHaveLength(2);
    expect(
      comparison.results.every(
        (j: { execution_mode: string }) => j.execution_mode === "mock",
      ),
    ).toBe(true);
    await expect(page.locator(".result")).toHaveCount(2);
    await expect(
      page.getByText("MOCK · synthetic answers", { exact: true }),
    ).toHaveCount(2);
  });
  test("import to persisted job to exported ranking and later outcome to not-ready evaluation", async ({
    page,
  }) => {
    const id = `synthetic-browser-${runId}`;
    const published = "2026-01-01T00:00:00Z";
    const observed = "2026-01-03T00:00:00Z";
    const fixture = {
      candidate_id: id,
      candidate_version: 1,
      text: `Synthetic corpus fixture ${runId}: A checked migration and its rollback rehearsal gave reviewers a concrete way to challenge a generated patch.`,
      language: "en",
      post_type: "original",
      author_id: "synthetic-browser-author",
      published_at: published,
      content_available_at: published,
      distribution: "organic",
      synthetic: true,
      provenance: "original_synthetic_browser_test",
    };
    await page.goto("/#corpus");
    await page
      .getByRole("combobox", { name: "Format", exact: true })
      .selectOption("jsonl");
    await page
      .getByRole("textbox", { name: "Candidate rows", exact: true })
      .fill(JSON.stringify(fixture));
    const previewed = responseFor(page, "/api/import");
    await page.getByRole("button", { name: "Preview & validate" }).click();
    const preview = await (await previewed).json();
    expect(preview.accepted).toBe(1);
    expect(preview.errors).toEqual([]);
    expect(preview.preview_only).toBe(true);
    const imported = responseFor(page, "/api/import");
    await page.getByRole("button", { name: "Import validated rows" }).click();
    expect((await (await imported).json()).accepted).toBe(1);
    await page
      .getByRole("combobox", { name: "Execution", exact: true })
      .selectOption("mock");
    await page.getByLabel(`Select ${id} version 1`, { exact: true }).check();
    const started = responseFor(page, "/api/jobs");
    await page.getByRole("button", { name: "Judge 1", exact: true }).click();
    const job = await (await started).json();
    expect(job.execution_mode).toBe("mock");
    const row = page.locator("tbody tr").filter({ hasText: id });
    await expect(
      row.getByRole("button", { name: "View judgment" }),
    ).toBeVisible({ timeout: 30_000 });
    const downloading = page.waitForEvent("download");
    await page.getByRole("link", { name: "Export JSONL" }).click();
    const downloaded = await downloading;
    const file = await downloaded.path();
    expect(file).toBeTruthy();
    const exported = (await readFile(file!, "utf8"))
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    const matching = exported.find((j) => j.candidate_id === id);
    expect(matching).toBeTruthy();
    expect(matching.execution_mode).toBe("mock");
    expect(
      matching.score_continuous === null ||
        typeof matching.score_continuous === "number",
    ).toBe(true);
    await page
      .getByRole("combobox", { name: "Candidate version", exact: true })
      .selectOption(`${id}:1`);
    await page
      .getByLabel("Observed at (local time)", { exact: true })
      .fill(localDate(observed));
    await page
      .getByLabel("Observed count · optional", { exact: true })
      .fill("12000");
    const attaching = responseFor(page, "/api/outcomes");
    await page.getByRole("button", { name: "Attach observation" }).click();
    const outcomeResponse = await attaching;
    expect(outcomeResponse.ok()).toBe(true);
    const outcome = await outcomeResponse.json();
    expect(outcome.views).toBe(12000);
    expect(outcome.synthetic).toBe(true);
    expect(outcome.likes).toBeNull();
    await page.getByRole("button", { name: /Experiments/ }).click();
    await page
      .getByRole("combobox", { name: "Dataset", exact: true })
      .selectOption("real");
    const evaluated = responseFor(page, "/api/experiments");
    await page
      .getByRole("button", { name: "Evaluate development only" })
      .click();
    const evaluation = await (await evaluated).json();
    expect(evaluation.status).toBe("not_ready");
    expect(evaluation.forecast_available).toBe(false);
    await expect(page.locator(".experiment-report")).toContainText("not_ready");
    await expect(page.locator(".experiment-report")).toContainText(
      "Not Ready Reasons",
    );
  });
  test("synthetic evaluation exposes evidence and bounded development-only discovery", async ({
    page,
  }) => {
    await page.goto("/#experiments");
    await page
      .getByRole("combobox", { name: "Dataset", exact: true })
      .selectOption("synthetic");
    const evaluated = responseFor(page, "/api/experiments");
    await page
      .getByRole("button", { name: "Run synthetic demonstration" })
      .click();
    const response = await evaluated;
    expect(response.ok()).toBe(true);
    const report = await response.json();
    expect(report.synthetic).toBe(true);
    expect(report.status).toBe("evaluated");
    expect(report.predictive_validation).toBe("not_established");
    expect(report.forecast_available).toBe(false);
    await expect(page.getByText("Same-cohort method comparison")).toBeVisible();
    await expect(
      page.getByText("SYNTHETIC DEMONSTRATION", { exact: true }),
    ).toBeVisible();
    await page
      .getByText("Explore proposals for this experiment · mock computation", {
        exact: true,
      })
      .click();
    const errors = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname ===
        `/api/discovery/${report.experiment_id}/errors`,
    );
    await page
      .getByRole("button", { name: "Inspect development errors" })
      .click();
    const development = await (await errors).json();
    expect(development.partition).toBe("selection");
    const proposals = [
      {
        feature_id: "browser_demonstrated_process",
        version: "synthetic_browser_v1",
        type: "noul",
        instructions:
          "Does candidate.text name a concrete repeatable action that a reader can try? Use only supplied candidate text, audience, and permitted context.",
        reviewed_by: "synthetic_browser_fixture",
        reviewed_at: "2026-01-01T00:00:00Z",
        hypothesis:
          "A narrow demonstrated-action question may add information to the existing semantic features; this test establishes wiring only.",
      },
    ];
    await page
      .getByLabel("Reviewed proposals (JSON array)", { exact: false })
      .fill(JSON.stringify(proposals));
    await page.getByLabel("Cost ceiling (USD)", { exact: true }).fill("0.10");
    await page.getByLabel("Maximum rows", { exact: true }).fill("50");
    await page.getByLabel("Maximum requests", { exact: true }).fill("50");
    const proposed = responseFor(page, "/api/discovery");
    await page
      .getByRole("button", { name: "Evaluate reviewed mock proposals" })
      .click();
    const proposalResponse = await proposed;
    expect(proposalResponse.ok()).toBe(true);
    const result = await proposalResponse.json();
    expect(result.synthetic).toBe(true);
    expect(result.test_exposed).toBe(false);
    expect(result.production_promoted).toBe(false);
    expect(result.request_count).toBeLessThanOrEqual(50);
    expect(result.charged_or_reserved_usd).toBe(0);
    await expect(
      page.getByText("Discovery audit, decisions, and validation", {
        exact: true,
      }),
    ).toBeVisible();
  });
});
