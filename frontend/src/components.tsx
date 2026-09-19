import { useState } from "react";
import { api, number, percent, readable } from "./api";
import type { Config, Factor, Inspection, Judgment } from "./types";
export function JsonView({
  value,
  label = "Inspect JSON",
  open = false,
}: {
  value: unknown;
  label?: string;
  open?: boolean;
}) {
  return (
    <details className="json-view" open={open}>
      <summary>{label}</summary>
      <pre tabIndex={0}>{JSON.stringify(value, null, 2) ?? "Unavailable"}</pre>
    </details>
  );
}
export function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="error" role="alert">
      <strong>Could not complete this action</strong>
      <p>{error}</p>
    </div>
  ) : null;
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-mark" aria-hidden="true">
        ↗
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Fact({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="fact">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function FactorRow({
  id,
  factor,
  config,
}: {
  id: string;
  factor: Factor;
  config: Config;
}) {
  const label = config.rubric.questions[id]?.label ?? readable(id);
  return (
    <details className="factor-row">
      <summary>
        <span>
          {label}
          <small>
            {factor.assessability === "assessable"
              ? factor.type === "noul"
                ? "Yes/no semantic judgment"
                : `Answer certainty: ${percent(factor.confidence)}`
              : readable(factor.assessability)}
          </small>
        </span>
        <strong>
          {factor.assessability !== "assessable"
            ? "—"
            : factor.type === "score"
              ? `${number(factor.score)} / 4`
              : factor.type === "noul"
                ? percent(factor.noul)
                : (factor.choice ?? "—")}
        </strong>
      </summary>
      <div className="factor-details">
        {factor.type === "score" && (
          <p>
            Raw Jev factor, on a zero-based 0–4 scale.
            {id === "aversion"
              ? " Higher means greater audience-aversion risk."
              : ""}
          </p>
        )}
        {Object.entries(factor.probabilities).map(([level, probability]) => (
          <div className="distribution" key={level}>
            <div>
              <span>
                {factor.legend[level] ??
                  config.rubric.questions[id]?.criteria[Number(level)] ??
                  level}
              </span>
              <strong>{percent(probability)}</strong>
            </div>
            <meter
              value={probability}
              min="0"
              max="1"
              aria-label={`Level ${level} probability`}
            />
          </div>
        ))}
        {factor.evidence_references.length > 0 && (
          <p>Evidence: {factor.evidence_references.join(", ")}</p>
        )}
        {factor.error && <p className="danger-text">{factor.error}</p>}
        <JsonView value={factor} label="Raw factor answer" />
      </div>
    </details>
  );
}
export function Result({
  judgment: j,
  config,
  compact = false,
  forecastAvailable = false,
}: {
  judgment: Judgment;
  config: Config;
  compact?: boolean;
  forecastAvailable?: boolean;
}) {
  const [inspector, setInspector] = useState<Inspection | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [forecast, setForecast] = useState<{
    available: boolean;
    breakout_probability?: number;
    score_1_to_5?: number;
    comparison_population?: string;
    reason?: string;
  } | null>(null);
  async function requestForecast() {
    setBusy(true);
    setError("");
    try {
      setForecast(await api(`/forecast/${encodeURIComponent(j.judgment_id)}`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function inspect() {
    setBusy(true);
    setError("");
    try {
      setInspector(
        await api<Inspection>(
          `/judgments/${encodeURIComponent(j.judgment_id)}`,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const names = ["", "Very low", "Low", "Moderate", "High", "Very high"];
  return (
    <article className="result" aria-label={`Judgment ${j.candidate_id}`}>
      <div className="result-top">
        <span className="eyebrow">Editorial potential</span>
        <div className="badges">
          <Badge tone={j.execution_mode === "mock" ? "amber" : "green"}>
            {j.execution_mode === "mock"
              ? "MOCK · synthetic answers"
              : "LIVE · Jev"}
          </Badge>
          <Badge tone={j.status === "scored" ? "green" : "amber"}>
            {j.status}
          </Badge>
        </div>
      </div>
      <div className="score-line">
        <strong className="score">{j.score_1_to_5 ?? "—"}</strong>
        <div>
          <span>
            {j.score_1_to_5 == null
              ? "No complete rating"
              : `${names[j.score_1_to_5]} potential`}
          </span>
          <small>
            {j.score_1_to_5 == null
              ? "See missing evidence and execution details."
              : `of 5 · continuous score ${number(j.score_continuous, 4)}`}
          </small>
        </div>
      </div>
      <p className="fine-print">
        Editorial potential — not a calibrated probability.
      </p>
      <div className="result-meta">
        <Fact
          label="Audience"
          value={`${readable(j.audience_id)} · v${j.audience_version}`}
        />
        <Fact label="Profile" value={readable(j.profile_id)} />
        <Fact
          label="Breakout probability"
          value={
            j.breakout_probability == null
              ? "Not established"
              : percent(j.breakout_probability)
          }
        />
        <Fact
          label="Evidence completeness"
          value={
            j.status === "scored"
              ? "Required factors available"
              : "Incomplete / unavailable"
          }
        />
      </div>
      <button
        className="secondary small"
        disabled={!forecastAvailable || busy}
        onClick={requestForecast}
      >
        {forecastAvailable
          ? "Inspect approved forecast"
          : "Forecast unavailable · not promoted"}
      </button>
      {forecast && (
        <section className="notice" aria-label="Forecast result">
          <h3>Evidence-gated forecast</h3>
          {forecast.available ? (
            <>
              <p>
                Breakout probability:{" "}
                <strong>{percent(forecast.breakout_probability)}</strong> · tier{" "}
                {forecast.score_1_to_5} / 5
              </p>
              <p>Comparison population: {forecast.comparison_population}</p>
              <p>
                Forecast tiers use frozen probability boundaries. Tier 5 does
                not mean an 80–100% probability.
              </p>
            </>
          ) : (
            <p>
              {forecast.reason ??
                "This judgment is not eligible for the promoted predictor."}
            </p>
          )}
          <JsonView
            value={forecast}
            label="Forecast predictor, calibration, and frozen tier boundaries"
          />
        </section>
      )}
      {j.review_flags.length > 0 && (
        <div className="notice">
          <strong>Review before interpreting</strong>
          <ul>
            {j.review_flags.map((flag) => (
              <li key={flag}>{readable(flag)}</li>
            ))}
          </ul>
        </div>
      )}
      {j.error_category && (
        <p className="error" role="status">
          Execution issue: {readable(j.error_category)}. An unavailable result
          is not a low score.
        </p>
      )}
      {!compact && (
        <>
          <h3 className="section-heading">The evidence behind the rating</h3>
          <p className="muted">
            Factor certainty describes Jev’s answer, not future performance.
            Expand a factor for its distribution and criteria.
          </p>
          <div className="factors">
            {Object.entries(j.factors).map(([id, f]) => (
              <FactorRow key={id} id={id} factor={f} config={config} />
            ))}
          </div>
          <div className="calculation">
            <Fact label="Unpenalized quality" value={number(j.quality, 4)} />
            <Fact
              label="Aversion adjustment"
              value={number(j.risk_penalty, 4)}
            />
            <Fact label="Calculation policy" value={j.rubric_version} />
          </div>
          <JsonView
            value={j.explanation}
            label="Rubric explanation and calculation"
            open
          />
        </>
      )}
      <div className="result-footer">
        <small>
          {j.cached ? "Cached judgment" : "New judgment"} · {j.attempt_count}{" "}
          attempt{j.attempt_count === 1 ? "" : "s"} ·{" "}
          {j.model_returned ?? j.model_requested}
        </small>
        <button className="secondary small" onClick={inspect} disabled={busy}>
          {busy ? "Loading…" : "Inspect run"}
        </button>
      </div>
      <ErrorBox error={error} />
      {inspector && (
        <section className="inspector" aria-label="Run inspector">
          <div className="panel-heading">
            <h3>Run inspector</h3>
            <button className="text-button" onClick={() => setInspector(null)}>
              Close inspector
            </button>
          </div>
          <p className="muted">
            Exact persisted request, allowed model state, question definitions,
            and run lineage.
          </p>
          <JsonView value={inspector.state} label="State sent to Jev" />
          <JsonView
            value={inspector.questions}
            label="Questions and rubric sent to Jev"
          />
          <JsonView value={inspector.request} label="Original request" />
          <JsonView
            value={inspector.judgment}
            label="Versions, cache, attempts, usage, calculation and lineage"
          />
        </section>
      )}
    </article>
  );
}
