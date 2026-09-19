import { useEffect, useId, useState } from "react";
import { api, number, percent, readable } from "./api";
import { Badge, Empty, ErrorBox, Fact, JsonView } from "./components";
import type { Report, Session } from "./types";
import Discovery from "./Discovery";
function reportId(report: Report): string {
  return String(report.experiment_id ?? report.id ?? "");
}
const record = (v: unknown): Report =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Report) : {};
const metric = (v: unknown) =>
  typeof v === "number" ? number(v, 4) : "Undefined";
function ReliabilityPlot({ name, bands }: { name: string; bands: unknown[] }) {
  const titleId = useId();
  const points = bands
    .map(record)
    .filter(
      (b) =>
        typeof b.rows === "number" &&
        b.rows > 0 &&
        typeof b.mean_probability === "number" &&
        Number.isFinite(b.mean_probability) &&
        b.mean_probability >= 0 &&
        b.mean_probability <= 1 &&
        typeof b.observed_rate === "number" &&
        Number.isFinite(b.observed_rate) &&
        b.observed_rate >= 0 &&
        b.observed_rate <= 1,
    );
  if (!points.length)
    return (
      <p className="muted">
        No nonempty probability bands are available to plot.
      </p>
    );
  const x = (probability: number) => 45 + 245 * probability;
  const y = (rate: number) => 215 - 180 * rate;
  return (
    <figure className="reliability-plot">
      <svg viewBox="0 0 320 265" role="img" aria-labelledby={titleId}>
        <title id={titleId}>
          {readable(name)} reliability: mean predicted probability versus
          observed outcome rate. Only nonempty bands are plotted.
        </title>
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line x1="45" y1={y(tick)} x2="290" y2={y(tick)} stroke="#dce2d9" />
            <text x="37" y={y(tick) + 3} textAnchor="end">
              {tick}
            </text>
            <text x={x(tick)} y="232" textAnchor="middle">
              {tick}
            </text>
          </g>
        ))}
        <line x1="45" y1="215" x2="290" y2="215" stroke="#687c68" />
        <line x1="45" y1="215" x2="45" y2="35" stroke="#687c68" />
        <line
          x1="45"
          y1="215"
          x2="290"
          y2="35"
          stroke="#9caa8e"
          strokeDasharray="5 5"
        />
        {points.map((band, index) => (
          <circle
            key={index}
            cx={x(band.mean_probability as number)}
            cy={y(band.observed_rate as number)}
            r="4"
            fill="#245d43"
          >
            <title>
              {String(band.rows)} observations; mean probability{" "}
              {metric(band.mean_probability)}; observed rate{" "}
              {metric(band.observed_rate)}
            </title>
          </circle>
        ))}
        <text x="168" y="253" textAnchor="middle">
          Mean predicted probability
        </text>
        <text transform="translate(13 125) rotate(-90)" textAnchor="middle">
          Observed outcome rate
        </text>
      </svg>
      <figcaption>
        Dashed diagonal: matching probability and observed rate. Each point is a
        nonempty band; exact values and sample sizes are below.
      </figcaption>
    </figure>
  );
}
function EvidenceSummary({ report }: { report: Report }) {
  const eligibility = record(report.eligibility);
  const counts = record(record(report.split_manifest).counts);
  const methods = record(report.metrics);
  return (
    <>
      <div className="evidence-facts">
        <Fact
          label="Eligible rows"
          value={String(eligibility.eligible_rows ?? "Unavailable")}
        />
        <Fact
          label="Positive outcomes"
          value={String(eligibility.positives ?? "Unavailable")}
        />
        <Fact
          label="Coverage"
          value={
            typeof eligibility.coverage === "number"
              ? percent(eligibility.coverage)
              : "Unavailable"
          }
        />
        <Fact
          label="Predictive validation"
          value={String(
            report.predictive_validation ?? "See promotion decision",
          )}
        />
      </div>
      {Object.keys(counts).length > 0 && (
        <div className="partition-grid">
          {Object.entries(counts).map(([name, value]) => (
            <div key={name}>
              <span>{readable(name)}</span>
              <strong>{String(record(value).rows ?? "—")}</strong>
              <small>{String(record(value).positives ?? "—")} positives</small>
            </div>
          ))}
        </div>
      )}
      {Object.keys(methods).length > 0 && (
        <>
          <h3>Same-cohort method comparison</h3>
          <p className="muted">
            Undefined metrics are unavailable, not zero. Lower Brier and log
            loss are better; ranking metrics describe the defined outcome task.
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Method</th>
                  <th>Rows</th>
                  <th>Brier ↓</th>
                  <th>Log loss ↓</th>
                  <th>PR-AUC ↑</th>
                  <th>ROC-AUC ↑</th>
                  <th>Top 10% lift</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(methods).map(([name, value]) => {
                  const m = record(value);
                  return (
                    <tr key={name}>
                      <td>{readable(name)}</td>
                      <td>{String(m.rows ?? "—")}</td>
                      <td>{metric(m.brier)}</td>
                      <td>{metric(m.log_loss)}</td>
                      <td>{metric(m.pr_auc)}</td>
                      <td>{metric(m.roc_auc)}</td>
                      <td>{metric(record(m.top_10pct).lift)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <h3 className="section-heading">Reliability by probability band</h3>
          {Object.entries(methods).map(([name, value]) => {
            const bands = record(value).reliability;
            if (!Array.isArray(bands) || !bands.length) return null;
            return (
              <details className="json-view" key={name}>
                <summary>{readable(name)} · calibration observations</summary>
                <ReliabilityPlot name={name} bands={bands} />
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Band</th>
                        <th>Rows</th>
                        <th>Mean probability</th>
                        <th>Observed rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bands.map((raw, index) => {
                        const band = record(raw);
                        return (
                          <tr key={index}>
                            <td>
                              {metric(band.lower)} – {metric(band.upper)}
                            </td>
                            <td>{String(band.rows ?? "—")}</td>
                            <td>{metric(band.mean_probability)}</td>
                            <td>{metric(band.observed_rate)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </details>
            );
          })}
        </>
      )}
    </>
  );
}
function ReportView({ report }: { report: Report }) {
  const important = [
    "not_ready_reasons",
    "eligibility",
    "dataset",
    "dataset_hash",
    "label_definition",
    "label_definition_id",
    "task",
    "split_manifest",
    "splits",
    "coverage",
    "exclusions",
    "models",
    "selection",
    "baselines",
    "metrics",
    "calibration",
    "subgroups",
    "author_holdout",
    "uncertainty",
    "score_bands",
    "failure_examples",
    "promotion",
    "promotion_gates",
    "audit",
    "limitations",
  ];
  const [all, setAll] = useState(false);
  return (
    <section className="panel experiment-report">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Experiment report</span>
          <h2>{reportId(report) || "Evaluation result"}</h2>
        </div>
        <Badge tone={report.synthetic ? "amber" : "green"}>
          {report.synthetic ? "SYNTHETIC DEMONSTRATION" : "PRIVATE EVALUATION"}
        </Badge>
      </div>
      <p className="notice">
        {report.synthetic
          ? "Synthetic results verify pipeline mechanics. They do not measure Jev’s predictive accuracy."
          : "Real-data evaluation remains private. A trained predictor requires the frozen promotion gates and explicit approval before forecast use."}
      </p>
      {report.status != null && (
        <p>
          <strong>Status:</strong> {String(report.status)}
        </p>
      )}
      <EvidenceSummary report={report} />
      {important
        .filter((key) => report[key] !== undefined)
        .map((key) => (
          <JsonView
            key={key}
            value={report[key]}
            label={readable(key)}
            open={[
              "not_ready_reasons",
              "promotion",
              "promotion_gates",
              "limitations",
            ].includes(key)}
          />
        ))}
      <button className="text-button" onClick={() => setAll(!all)}>
        {all ? "Hide complete record" : "Inspect complete experiment record"}
      </button>
      {all && (
        <JsonView value={report} label="All persisted report fields" open />
      )}
    </section>
  );
}
export default function Experiments({ session }: { session: Session }) {
  const [reports, setReports] = useState<Report[]>([]);
  const [active, setActive] = useState<Report | null>(null);
  const [synthetic, setSynthetic] = useState(true);
  const [task, setTask] = useState("breakout_48h_v1");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api<Report[]>("/experiments")
      .then(setReports)
      .catch((e) => setError(e.message));
  }, []);
  async function run() {
    setError("");
    setBusy("run");
    try {
      const report = await api<Report>("/experiments", { synthetic, task });
      setActive(report);
      setReports(await api<Report[]>("/experiments"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function inspect(id: string) {
    setError("");
    setBusy(id);
    try {
      setActive(await api<Report>(`/experiments/${encodeURIComponent(id)}`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <>
      <header className="workspace-header">
        <div>
          <span className="eyebrow">03 / The evaluation lab</span>
          <h1>A rating is a hypothesis.</h1>
          <p>
            Keep the test untouched. Compare baselines. Earn every predictive
            claim.
          </p>
        </div>
        <Badge tone="amber">
          Forecast {session.forecast.available ? "available" : "not promoted"}
        </Badge>
      </header>
      <ErrorBox error={error} />
      <section className="forecast-gate panel">
        <div className="gate-icon" aria-hidden="true">
          ◎
        </div>
        <div>
          <h2>
            {session.forecast.available
              ? "Forecast eligibility has been recorded"
              : "Forecasts need evidence first"}
          </h2>
          <p>
            Editorial potential, calibrated breakout probability, and Jev’s
            answer certainty describe different things.
          </p>
          {session.forecast.reasons.length > 0 ? (
            <ul>
              {session.forecast.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              Inspect the experiment promotion decision for data, calibration,
              and approval requirements.
            </p>
          )}
        </div>
      </section>
      <div className="experiment-grid">
        <section className="panel">
          <h2>Run a reproducible evaluation</h2>
          <p className="muted">
            Chronological partitions, outcome maturation, baseline comparisons,
            and calibration are enforced in the backend.
          </p>
          <label>
            Dataset
            <select
              value={synthetic ? "synthetic" : "real"}
              onChange={(e) => setSynthetic(e.target.value === "synthetic")}
            >
              <option value="synthetic">
                Synthetic mechanics demonstration
              </option>
              <option value="real">Private real corpus</option>
            </select>
          </label>
          <label>
            Outcome task
            <select value={task} onChange={(e) => setTask(e.target.value)}>
              <option value="breakout_48h_v1">
                Breakout within 48 hours · absolute + relative
              </option>
              <option value="absolute_48h_v1">
                Absolute reach within 48 hours · cold start
              </option>
            </select>
          </label>
          <p className="fine-print">
            Breakout requires ≥10,000 views and ≥10× the author’s qualifying
            historical baseline. Missing history makes the relative task
            unavailable.
          </p>
          <button className="primary" disabled={!!busy} onClick={run}>
            {busy === "run"
              ? "Evaluating…"
              : synthetic
                ? "Run synthetic demonstration"
                : "Evaluate eligible real records"}
          </button>
          <p className="fine-print">
            Final holdouts are for frozen candidates, not repeated tuning.
            Evaluation does not automatically promote a forecast.
          </p>
        </section>
        <section className="panel">
          <h2>Experiment history</h2>
          {!reports.length ? (
            <Empty title="No evaluated datasets yet">
              Start with a synthetic demonstration, or attach eligible real
              outcomes in Corpus.
            </Empty>
          ) : (
            <div className="experiment-list">
              {reports.map((r, index) => (
                <button
                  key={reportId(r) || index}
                  className="experiment-item"
                  onClick={() => inspect(reportId(r))}
                  disabled={!!busy}
                >
                  <span>
                    <strong>{reportId(r) || `Experiment ${index + 1}`}</strong>
                    <small>
                      {String(
                        r.task ??
                          r.label_definition_id ??
                          "Versioned outcome task",
                      )}{" "}
                      · {String(r.status ?? "Persisted")}
                    </small>
                  </span>
                  <Badge tone={r.synthetic ? "amber" : "neutral"}>
                    {r.synthetic ? "Synthetic" : "Private"}
                  </Badge>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
      {active && <ReportView report={active} />}
      <Discovery experimentId={active ? reportId(active) : ""} />
    </>
  );
}
