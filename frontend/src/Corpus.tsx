import { useCallback, useEffect, useState } from "react";
import { api, number, readable } from "./api";
import { Badge, Empty, ErrorBox, JsonView, Result } from "./components";
import { Controls } from "./Judge";
import type {
  Candidate,
  Config,
  Job,
  Judgment,
  Mode,
  Report,
  Session,
} from "./types";
const key = (c: Candidate) => `${c.candidate_id}:${c.candidate_version}`;
export default function Corpus({
  config,
  session,
}: {
  config: Config;
  session: Session;
}) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [judgments, setJudgments] = useState<Judgment[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [audience, setAudience] = useState("production_ai_coding");
  const [profile, setProfile] = useState("text_core_v1");
  const [mode, setMode] = useState<Mode>("mock");
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState(false);
  const [content, setContent] = useState("");
  const [format, setFormat] = useState("csv");
  const [mapping, setMapping] = useState("{}");
  const [preview, setPreview] = useState<Report | null>(null);
  const [imported, setImported] = useState<Report | null>(null);
  const [outcomeCandidate, setOutcomeCandidate] = useState("");
  const [source, setSource] = useState("manual");
  const [metric, setMetric] = useState("views");
  const [views, setViews] = useState("");
  const [likes, setLikes] = useState("");
  const [hours, setHours] = useState("48");
  const [observed, setObserved] = useState("");
  const [distribution, setDistribution] = useState("organic");
  const [outcomeContent, setOutcomeContent] = useState("");
  const [outcomeFormat, setOutcomeFormat] = useState("jsonl");
  const [outcomeResponse, setOutcomeResponse] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [inspected, setInspected] = useState<Judgment | null>(null);
  const [loaded, setLoaded] = useState(false);
  const reload = useCallback(async () => {
    const [c, j, b] = await Promise.all([
      api<Candidate[]>("/candidates"),
      api<Judgment[]>("/judgments"),
      api<Job[]>("/jobs"),
    ]);
    setCandidates(c);
    setJudgments(j);
    setJobs(b);
    setLoaded(true);
  }, []);
  useEffect(() => {
    reload().catch((e) => setError(e.message));
  }, [reload]);
  useEffect(() => {
    if (
      !jobs.some((job) =>
        ["pending", "running", "queued", "processing"].includes(job.status),
      )
    )
      return;
    const timer = setInterval(() => {
      reload().catch((e) => setError(e.message));
    }, 2000);
    return () => clearInterval(timer);
  }, [jobs, reload]);
  async function action(name: string, fn: () => Promise<void>) {
    setError("");
    setBusy(name);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function importRows(isPreview: boolean) {
    await action(isPreview ? "preview" : "import", async () => {
      const fields = JSON.parse(mapping);
      if (!fields || Array.isArray(fields) || typeof fields !== "object")
        throw new Error("Field mapping must be a JSON object.");
      const result = await api<Report>("/import", {
        content,
        format,
        mapping: fields,
        preview: isPreview,
      });
      if (isPreview) {
        setPreview(result);
        setImported(null);
      } else {
        setImported(result);
        setPreview(null);
        await reload();
      }
    });
  }
  const version = config.audiences.find(
    (a) => a.audience_id === audience,
  )?.version;
  const latest = new Map<string, Judgment>();
  [...judgments]
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .filter(
      (j) =>
        j.audience_id === audience &&
        j.audience_version === version &&
        j.profile_id === profile &&
        j.execution_mode === mode &&
        j.rubric_version === config.rubric.version,
    )
    .forEach((j) => latest.set(`${j.candidate_id}:${j.candidate_version}`, j));
  const rows = candidates
    .filter(
      (c) =>
        (!search ||
          `${c.text} ${c.candidate_id}`
            .toLowerCase()
            .includes(search.toLowerCase())) &&
        (status === "all" ||
          (latest.get(key(c))?.status ?? "unjudged") === status),
    )
    .sort((a, b) =>
      sort
        ? (latest.get(key(b))?.score_continuous ?? -1) -
          (latest.get(key(a))?.score_continuous ?? -1)
        : 0,
    );
  async function attach(e: React.FormEvent) {
    e.preventDefault();
    await action("outcome", async () => {
      const c = candidates.find((c) => key(c) === outcomeCandidate);
      if (!c) throw new Error("Select a candidate.");
      const when = new Date(observed).toISOString();
      setOutcomeResponse(
        await api<Report>("/outcomes", {
          candidate_id: c.candidate_id,
          candidate_version: c.candidate_version,
          source,
          metric,
          observed_at: when,
          available_at: when,
          elapsed_hours: Number(hours),
          views: views === "" ? null : Number(views),
          likes: likes === "" ? null : Number(likes),
          distribution,
          synthetic: c.synthetic,
          provenance: "manual_ui",
        }),
      );
    });
  }
  const filters = new URLSearchParams({
    audience_id: audience,
    profile_id: profile,
    execution_mode: mode,
    audience_version: version ?? "",
    rubric_version: config.rubric.version,
  });
  return (
    <>
      <header className="workspace-header">
        <div>
          <span className="eyebrow">02 / The corpus machine</span>
          <h1>Turn a collection into evidence.</h1>
          <p>
            Import, judge, inspect, and attach what happened later. Every row
            keeps its lineage.
          </p>
        </div>
        <button
          className="secondary"
          onClick={() => action("refresh", reload)}
          disabled={!!busy}
        >
          Refresh records
        </button>
      </header>
      <ErrorBox error={error} />
      <div className="corpus-top">
        <section className="panel">
          <div className="panel-heading">
            <h2>Import candidates</h2>
            <Badge>CSV / JSONL</Badge>
          </div>
          <p className="muted">
            Only explicit fields enter the prediction state. Source rows and
            validation errors stay in local storage.
          </p>
          <div className="two-cols">
            <label>
              Format
              <select
                value={format}
                onChange={(e) => {
                  setFormat(e.target.value);
                  setPreview(null);
                }}
              >
                <option value="csv">CSV</option>
                <option value="jsonl">JSONL</option>
              </select>
            </label>
            <label>
              Read a local file
              <input
                type="file"
                accept=".csv,.jsonl,.ndjson"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) {
                    if (f.size > 5_000_000) {
                      setError(
                        "Use files under 5 MB in the browser. Larger imports can use the CLI.",
                      );
                      return;
                    }
                    void f
                      .text()
                      .then((data) => {
                        setContent(data);
                        setFormat(f.name.endsWith(".csv") ? "csv" : "jsonl");
                        setPreview(null);
                      })
                      .catch((error) => setError(error.message));
                  }
                }}
              />
            </label>
          </div>
          <label>
            Candidate rows
            <textarea
              className="code-input"
              rows={5}
              value={content}
              onChange={(e) => {
                setContent(e.target.value);
                setPreview(null);
              }}
              placeholder={
                'candidate_id,text,author_id\nmy-draft,"Your candidate text",author-1'
              }
              spellCheck={false}
            />
          </label>
          <details>
            <summary>Field mapping</summary>
            <label>
              Canonical field → source column (JSON)
              <textarea
                className="code-input"
                value={mapping}
                onChange={(e) => {
                  setMapping(e.target.value);
                  setPreview(null);
                }}
                spellCheck={false}
              />
              <small>
                Example: {`{"text":"tweet", "candidate_id":"id"}`}. Empty
                mapping uses canonical field names.
              </small>
            </label>
          </details>
          <div className="button-row">
            <button
              className="secondary"
              disabled={!!busy || !content.trim()}
              onClick={() => importRows(true)}
            >
              {busy === "preview" ? "Validating…" : "Preview & validate"}
            </button>
            <button
              className="primary"
              disabled={!!busy || !preview}
              onClick={() => importRows(false)}
            >
              {busy === "import" ? "Importing…" : "Import validated rows"}
            </button>
          </div>
          {preview && (
            <JsonView
              value={preview}
              label="Validation, quality, and mapped row preview"
              open
            />
          )}
          {imported && (
            <div role="status">
              <h3>Import complete</h3>
              <JsonView value={imported} label="Import receipt" open />
            </div>
          )}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>Batch jobs</h2>
            <span className="muted">Persisted &amp; resumable</span>
          </div>
          {!jobs.length ? (
            <Empty title="No jobs yet">
              Select imported candidates below, then run a bounded batch.
            </Empty>
          ) : (
            <div className="job-list">
              {jobs.map((job) => (
                <article className="job" key={job.job_id}>
                  <div className="panel-heading">
                    <strong>{job.job_id.slice(0, 8)}</strong>
                    <Badge
                      tone={job.status === "completed" ? "green" : "amber"}
                    >
                      {job.status}
                    </Badge>
                  </div>
                  <p>
                    {
                      job.items.filter((i) =>
                        [
                          "scored",
                          "completed",
                          "partial",
                          "abstained",
                          "cached",
                        ].includes(i.status),
                      ).length
                    }{" "}
                    / {job.items.length} items resolved
                  </p>
                  <div className="button-row">
                    <button
                      className="secondary small"
                      disabled={
                        !!busy || ["completed", "running"].includes(job.status)
                      }
                      onClick={() =>
                        action("resume", async () => {
                          await api(
                            `/jobs/${encodeURIComponent(job.job_id)}/resume`,
                            {},
                          );
                          await reload();
                        })
                      }
                    >
                      Resume job
                    </button>
                    <button
                      className="text-button"
                      disabled={
                        !!busy ||
                        ["completed", "cancelled"].includes(job.status)
                      }
                      onClick={() =>
                        action("cancel", async () => {
                          await api(
                            `/jobs/${encodeURIComponent(job.job_id)}/cancel`,
                            {},
                          );
                          await reload();
                        })
                      }
                    >
                      Cancel job
                    </button>
                  </div>
                  <JsonView
                    value={job}
                    label="Inspect item status and failures"
                  />
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
      <section className="panel corpus-table">
        <div className="panel-heading">
          <div>
            <h2>Candidate library</h2>
            <p className="muted">
              {candidates.length} local candidate version
              {candidates.length === 1 ? "" : "s"} · {judgments.length}{" "}
              persisted judgment{judgments.length === 1 ? "" : "s"}
            </p>
          </div>
          <div className="button-row">
            <a
              className="secondary small"
              href={`/api/export?${filters}&format=csv`}
              download
            >
              Export CSV
            </a>
            <a
              className="secondary small"
              href={`/api/export?${filters}&format=jsonl`}
              download
            >
              Export JSONL
            </a>
          </div>
        </div>
        <Controls
          {...{
            config,
            audience,
            setAudience,
            profile,
            setProfile,
            mode,
            setMode,
            session,
          }}
        />
        <p className="fine-print">
          Displayed scores share the selected audience version, profile,
          execution mode, and current rubric. Context and reference sets may
          still differ; inspect before interpreting rank. Exports use the same
          audience/version, profile, execution, and rubric filters and preserve
          decimals.
        </p>
        <div className="library-toolbar">
          <label>
            Find candidates
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Text or candidate ID"
            />
          </label>
          <label>
            Status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {[
                "all",
                "unjudged",
                "scored",
                "partial",
                "abstained",
                "failed",
              ].map((s) => (
                <option key={s} value={s}>
                  {readable(s)}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={sort}
              onChange={(e) => setSort(e.target.checked)}
            />
            Sort comparable scores
          </label>
          <button
            className="primary"
            disabled={!!busy || !selected.length}
            onClick={() =>
              action("batch", async () => {
                await api("/jobs", {
                  candidate_ids: selected,
                  audience_id: audience,
                  profile_id: profile,
                  execution_mode: mode,
                });
                setSelected([]);
                await reload();
              })
            }
          >
            {busy === "batch"
              ? "Starting…"
              : `Judge ${selected.length || "selected"}`}
          </button>
        </div>
        {!rows.length ? (
          <Empty
            title={loaded ? "No candidates match" : "Loading your corpus…"}
          >
            {candidates.length
              ? "Change the filters to view more records."
              : "Import your first CSV or JSONL file above."}
          </Empty>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      aria-label="Select all visible candidates"
                      checked={
                        rows.length > 0 &&
                        rows.every((c) => selected.includes(key(c)))
                      }
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...new Set([...selected, ...rows.map(key)])]
                            : selected.filter(
                                (k) => !rows.some((c) => key(c) === k),
                              ),
                        )
                      }
                    />
                  </th>
                  <th>Candidate</th>
                  <th>Editorial score</th>
                  <th>Status</th>
                  <th>Inspect</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const j = latest.get(key(c));
                  return (
                    <tr key={key(c)}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${c.candidate_id} version ${c.candidate_version}`}
                          checked={selected.includes(key(c))}
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, key(c)]
                                : selected.filter((k) => k !== key(c)),
                            )
                          }
                        />
                      </td>
                      <td className="candidate-cell">
                        <p>{c.text}</p>
                        <small>
                          {c.candidate_id} · v{c.candidate_version} ·{" "}
                          {c.synthetic ? "Synthetic" : "User-supplied"}
                        </small>
                      </td>
                      <td className="numeric">
                        {j?.score_1_to_5 == null ? (
                          "—"
                        ) : (
                          <>
                            <strong>{j.score_1_to_5} / 5</strong>
                            <small>{number(j.score_continuous, 4)}</small>
                          </>
                        )}
                      </td>
                      <td>
                        <Badge
                          tone={j?.status === "scored" ? "green" : "neutral"}
                        >
                          {j?.status ?? "Unjudged"}
                        </Badge>
                      </td>
                      <td>
                        {j ? (
                          <button
                            className="text-button"
                            onClick={() => setInspected(j)}
                          >
                            View judgment
                          </button>
                        ) : (
                          <span className="muted">No judgment</span>
                        )}
                        <details>
                          <summary>Record</summary>
                          <pre>{JSON.stringify(c, null, 2)}</pre>
                        </details>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {inspected && (
        <section className="panel">
          <div className="panel-heading">
            <h2>Selected judgment</h2>
            <button className="text-button" onClick={() => setInspected(null)}>
              Close judgment
            </button>
          </div>
          <Result
            judgment={inspected}
            config={config}
            forecastAvailable={session.forecast.available}
          />
        </section>
      )}
      <section className="panel outcomes">
        <div className="panel-heading">
          <h2>Attach later outcomes</h2>
          <Badge>Observations ≠ annotations</Badge>
        </div>
        <p className="muted">
          Missing values stay missing. Enter the actual measurement window;
          seven-day views cannot become a 48-hour label.
        </p>
        <div className="two-cols wide-gap">
          <form onSubmit={attach}>
            <label>
              Candidate version
              <select
                required
                value={outcomeCandidate}
                onChange={(e) => setOutcomeCandidate(e.target.value)}
              >
                <option value="">Select a candidate…</option>
                {candidates.map((c) => (
                  <option key={key(c)} value={key(c)}>
                    {c.candidate_id} · v{c.candidate_version}
                  </option>
                ))}
              </select>
            </label>
            <div className="two-cols">
              <label>
                Metric source
                <input
                  required
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                />
              </label>
              <label>
                Metric
                <select
                  value={metric}
                  onChange={(e) => setMetric(e.target.value)}
                >
                  <option value="views">Views</option>
                  <option value="impressions">Impressions</option>
                </select>
              </label>
              <label>
                Observed at (local time)
                <input
                  required
                  type="datetime-local"
                  value={observed}
                  onChange={(e) => setObserved(e.target.value)}
                />
              </label>
              <label>
                Hours since publication
                <input
                  required
                  type="number"
                  min="0"
                  step="any"
                  value={hours}
                  onChange={(e) => setHours(e.target.value)}
                />
              </label>
              <label>
                Observed count · optional
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={views}
                  onChange={(e) => setViews(e.target.value)}
                  placeholder="Unknown"
                />
              </label>
              <label>
                Likes · optional
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={likes}
                  onChange={(e) => setLikes(e.target.value)}
                  placeholder="Unknown"
                />
              </label>
            </div>
            <label>
              Distribution
              <select
                value={distribution}
                onChange={(e) => setDistribution(e.target.value)}
              >
                {["organic", "paid", "giveaway", "unknown"].map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </label>
            <p className="fine-print">
              Availability is recorded as the observation timestamp. Historical
              imports can specify a separate available_at.
            </p>
            <button className="primary" disabled={!!busy || !candidates.length}>
              {busy === "outcome" ? "Saving…" : "Attach observation"}
            </button>
          </form>
          <div>
            <h3>Import outcome observations</h3>
            <label>
              Outcome format
              <select
                value={outcomeFormat}
                onChange={(e) => setOutcomeFormat(e.target.value)}
              >
                <option value="jsonl">JSONL</option>
                <option value="csv">CSV</option>
              </select>
            </label>
            <label>
              Outcome rows
              <textarea
                className="code-input"
                rows={8}
                value={outcomeContent}
                onChange={(e) => setOutcomeContent(e.target.value)}
                placeholder="Canonical outcome observation records"
                spellCheck={false}
              />
            </label>
            <p className="fine-print">
              Required: candidate_id, source, observed_at, available_at,
              elapsed_hours. Use ISO timestamps with a timezone.
            </p>
            <button
              className="secondary"
              disabled={!!busy || !outcomeContent.trim()}
              onClick={() =>
                action("outcome-import", async () => {
                  setOutcomeResponse(
                    await api<Report>("/outcomes/import", {
                      content: outcomeContent,
                      format: outcomeFormat,
                    }),
                  );
                })
              }
            >
              Import observations
            </button>
            {outcomeResponse && (
              <div role="status">
                <h3>Outcome receipt</h3>
                <JsonView
                  value={outcomeResponse}
                  label="Saved observation / import results"
                  open
                />
              </div>
            )}
          </div>
        </div>
      </section>
    </>
  );
}
