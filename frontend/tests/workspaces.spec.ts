import { expect, test, type Page } from "@playwright/test";
const audience = {
  audience_id: "production_ai_coding",
  version: "1",
  description: "Synthetic test audience.",
  interests: ["tests"],
  assumed_knowledge: ["Git"],
  examples: ["Test example"],
  assumptions: ["Synthetic test persona"],
};
const config = {
  audiences: [audience],
  rubric: {
    version: "rubric_v1",
    profiles: {
      text_core_v1: ["relevance"],
      reference_enriched_v1: ["relevance", "distinctiveness"],
    },
    questions: {
      relevance: {
        label: "Audience relevance",
        criteria: ["None", "Adjacent", "General", "Specific", "Central"],
      },
    },
  },
  limits: { ceiling: 1 },
};
const session = {
  csrf_token: "synthetic-csrf",
  live_configured: false,
  spending: { limit_usd: 1, spent_usd: 0 },
  forecast: {
    available: false,
    reasons: ["Real representative outcomes and manual approval are required."],
  },
};
const factor = {
  question_id: "relevance",
  type: "score",
  assessability: "assessable",
  score: 3.25,
  choice: null,
  noul: null,
  confidence: 0.81,
  probabilities: { "0": 0, "1": 0, "2": 0.1, "3": 0.55, "4": 0.35 },
  legend: {
    "0": "None",
    "1": "Adjacent",
    "2": "General",
    "3": "Specific",
    "4": "Central",
  },
  evidence_references: [],
};
const judgment = {
  judgment_id: "synthetic-judgment",
  candidate_id: "synthetic-candidate",
  candidate_version: 1,
  status: "scored",
  execution_mode: "mock",
  mode: "editorial",
  profile_id: "text_core_v1",
  audience_id: "production_ai_coding",
  audience_version: "1",
  rubric_version: "rubric_v1",
  score_continuous: 3.775,
  score_1_to_5: 4,
  quality: 0.75,
  risk_penalty: 0.05,
  breakout_probability: null,
  calibration_status: "not_established",
  factors: { relevance: factor },
  review_flags: ["synthetic_mock_answers"],
  explanation: {
    strongest: ["relevance"],
    calculation: "Server-owned test result",
  },
  provenance: { synthetic: true },
  model_requested: "jev-1.13.0",
  model_returned: "mock",
  sdk_version: "0.7.0",
  input_hash: "synthetic",
  reference_set_hash: "synthetic",
  code_commit: "test-fixture",
  created_at: "2026-09-18T12:00:00Z",
  estimated_cost: 0,
  latency: 0,
  attempt_count: 1,
  error_category: null,
  cached: false,
};
async function base(page: Page) {
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      json:
        path === "/api/session"
          ? session
          : path === "/api/config"
            ? config
            : [],
    });
  });
}
test("paste to result, distributions, CSRF and safe inspector", async ({
  page,
}) => {
  await base(page);
  let request: Record<string, unknown> = {};
  await page.route("**/api/judge", async (route) => {
    expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-csrf");
    request = route.request().postDataJSON();
    await route.fulfill({ json: judgment });
  });
  await page.route("**/api/judgments/synthetic-judgment", (route) =>
    route.fulfill({
      json: {
        judgment,
        request: {},
        state: { text: '<img src=x onerror="window.compromised=true">' },
        questions: { criteria: ["Synthetic criterion"] },
      },
    }),
  );
  await page.goto("/");
  await page
    .getByLabel("Candidate", { exact: true })
    .fill("An original synthetic workflow example.");
  await page.getByRole("button", { name: "Judge candidate" }).click();
  await expect(page.getByText("High potential", { exact: true })).toBeVisible();
  expect(request.execution_mode).toBe("mock");
  expect(request.audience_id).toBe("production_ai_coding");
  await expect(page.getByText("of 5 · continuous score 3.7750")).toBeVisible();
  await expect(
    page.getByText("Not established", { exact: true }),
  ).toBeVisible();
  await page
    .locator("summary")
    .filter({ hasText: "Audience relevance" })
    .click();
  await expect(page.getByText("55.0%", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Inspect run" }).click();
  await page.getByText("State sent to Jev", { exact: true }).click();
  await expect(
    page.locator("pre").filter({ hasText: "window.compromised" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      Boolean((window as unknown as { compromised?: boolean }).compromised),
    ),
  ).toBe(false);
  await expect(page.locator("img")).toHaveCount(0);
});
test("failed executions never become low ratings", async ({ page }) => {
  await base(page);
  await page.route("**/api/judge", (route) =>
    route.fulfill({
      json: {
        ...judgment,
        status: "failed",
        score_1_to_5: null,
        score_continuous: null,
        quality: null,
        risk_penalty: null,
        factors: {},
        review_flags: [],
        error_category: "budget_exceeded",
      },
    }),
  );
  await page.goto("/");
  await page
    .getByLabel("Candidate", { exact: true })
    .fill("Synthetic budget-stop example.");
  await page.getByRole("button", { name: "Judge candidate" }).click();
  await expect(
    page.getByText("No complete rating", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Execution issue: Budget Exceeded.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("Very low potential", { exact: true }),
  ).toHaveCount(0);
});
test("comparison sends independent candidates with explicit mode", async ({
  page,
}) => {
  await base(page);
  await page.route("**/api/compare", (route) => {
    const body = route.request().postDataJSON();
    expect(
      body.requests.map(
        (r: { candidate: { text: string } }) => r.candidate.text,
      ),
    ).toEqual(["Synthetic variant A", "Synthetic variant B"]);
    return route.fulfill({
      json: {
        results: [
          judgment,
          {
            ...judgment,
            judgment_id: "variant-b",
            candidate_id: "variant-b",
            score_1_to_5: 3,
            score_continuous: 3.1,
          },
        ],
        comparable: true,
        limitations: ["Reference context is shared."],
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Compare variants →" }).click();
  await page
    .getByLabel("Candidate A", { exact: true })
    .fill("Synthetic variant A");
  await page
    .getByLabel("Candidate B", { exact: true })
    .fill("Synthetic variant B");
  await page
    .getByRole("button", { name: "Compare variants", exact: true })
    .click();
  await expect(page.getByText("High potential", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Moderate potential", { exact: true }),
  ).toBeVisible();
});
test("preview, import, batch, nullable outcomes and filtered export", async ({
  page,
}) => {
  await base(page);
  const candidate = {
    candidate_id: "synthetic-candidate",
    candidate_version: 1,
    text: "Original synthetic corpus candidate.",
    language: "en",
    post_type: "original",
    author_id: "synthetic-author",
    synthetic: true,
  };
  let imported = false;
  await page.route("**/api/candidates", (route) =>
    route.fulfill({ json: imported ? [candidate] : [] }),
  );
  await page.route("**/api/judgments", (route) =>
    route.fulfill({ json: imported ? [judgment] : [] }),
  );
  await page.route("**/api/import", (route) => {
    const body = route.request().postDataJSON();
    if (!body.preview) imported = true;
    return route.fulfill({
      json: {
        import_id: "synthetic-import",
        accepted: 1,
        errors: [],
        quality: { synthetic: true },
        preview: [candidate],
      },
    });
  });
  await page.route("**/api/jobs", (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON().candidate_ids).toEqual([
        "synthetic-candidate:1",
      ]);
      return route.fulfill({
        json: { job_id: "synthetic-job", status: "completed", items: [] },
      });
    }
    return route.fulfill({ json: [] });
  });
  await page.route("**/api/outcomes", (route) => {
    const body = route.request().postDataJSON();
    expect(body.views).toBeNull();
    expect(body.likes).toBeNull();
    expect(body.synthetic).toBe(true);
    return route.fulfill({
      json: { ...body, observation_id: "synthetic-observation" },
    });
  });
  await page.goto("/#corpus");
  await expect(
    page.getByRole("button", { name: "Import validated rows" }),
  ).toBeDisabled();
  await page
    .getByLabel("Candidate rows", { exact: true })
    .fill("candidate_id,text\nsynthetic-candidate,Synthetic candidate");
  await page.getByRole("button", { name: "Preview & validate" }).click();
  await expect(
    page.getByText("Validation, quality, and mapped row preview"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Import validated rows" }).click();
  await expect(page.getByText(candidate.text, { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Export CSV" })).toHaveAttribute(
    "href",
    /audience_id=production_ai_coding&profile_id=text_core_v1&execution_mode=mock&audience_version=1&rubric_version=rubric_v1&format=csv/,
  );
  await page.getByLabel("Select synthetic-candidate version 1").check();
  await page.getByRole("button", { name: "Judge 1", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Candidate version", exact: true })
    .selectOption("synthetic-candidate:1");
  await page
    .getByLabel("Observed at (local time)", { exact: true })
    .fill("2026-09-18T12:00");
  await page.getByRole("button", { name: "Attach observation" }).click();
  await expect(
    page.getByText("Outcome receipt", { exact: true }),
  ).toBeVisible();
});
test("experiment evidence is visibly synthetic and cannot promote itself", async ({
  page,
}) => {
  await base(page);
  const report = {
    experiment_id: "synthetic-experiment",
    status: "evaluated",
    synthetic: true,
    task: "breakout_48h_v1",
    predictive_validation: "not_established",
    eligibility: { eligible_rows: 100, positives: 20, coverage: 1 },
    split_manifest: {
      counts: {
        train: { rows: 50, positives: 10 },
        calibration: { rows: 20, positives: 4 },
        test: { rows: 30, positives: 6 },
      },
    },
    metrics: {
      metadata_only: {
        rows: 30,
        brier: 0.2,
        log_loss: 0.5,
        pr_auc: 0.2,
        roc_auc: 0.5,
        top_10pct: { lift: 1 },
      },
    },
    promotion: {
      eligible: false,
      reasons: ["Synthetic demonstration cannot earn promotion."],
    },
    limitations: ["Mechanics only."],
  };
  await page.route("**/api/experiments", (route) =>
    route.fulfill({ json: route.request().method() === "POST" ? report : [] }),
  );
  await page.goto("/#experiments");
  await page
    .getByRole("button", { name: "Run synthetic demonstration" })
    .click();
  await expect(
    page.getByText("SYNTHETIC DEMONSTRATION", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Same-cohort method comparison")).toBeVisible();
  await expect(
    page.getByText("Synthetic demonstration cannot earn promotion.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Forecast not promoted", { exact: true }),
  ).toBeVisible();
});
test("connection failures are recoverable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await base(page);
  let failed = true;
  await page.route("**/api/session", (route) =>
    failed
      ? route.fulfill({
          status: 503,
          json: { detail: "Synthetic backend unavailable" },
        })
      : route.fulfill({ json: session }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Synthetic backend unavailable",
  );
  failed = false;
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(page.getByLabel("Candidate", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^Experiments/ }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
