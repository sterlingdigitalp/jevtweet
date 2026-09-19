import { useState } from "react";
import { api, readable } from "./api";
import { Badge, Empty, ErrorBox, JsonView, Result } from "./components";
import type { Config, JudgeRequest, Judgment, Mode, Session } from "./types";
const localTime = () =>
  new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
export function Controls({
  config,
  audience,
  setAudience,
  profile,
  setProfile,
  mode,
  setMode,
  session,
}: {
  config: Config;
  audience: string;
  setAudience: (v: string) => void;
  profile: string;
  setProfile: (v: string) => void;
  mode: Mode;
  setMode: (v: Mode) => void;
  session: Session;
}) {
  return (
    <div className="controls">
      <label>
        Audience
        <select value={audience} onChange={(e) => setAudience(e.target.value)}>
          {config.audiences.map((a) => (
            <option key={a.audience_id} value={a.audience_id}>
              {readable(a.audience_id)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Editorial profile
        <select value={profile} onChange={(e) => setProfile(e.target.value)}>
          {Object.keys(config.rubric.profiles).map((p) => (
            <option key={p} value={p}>
              {readable(p)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Execution
        <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
          <option value="mock">Mock · offline demonstration</option>
          <option value="live" disabled={!session.live_configured}>
            Live Jev
            {!session.live_configured ? " · credentials unavailable" : ""}
          </option>
        </select>
      </label>
    </div>
  );
}
export default function Judge({
  config,
  session,
}: {
  config: Config;
  session: Session;
}) {
  const [text, setText] = useState("");
  const [variant, setVariant] = useState("");
  const [comparison, setComparison] = useState(false);
  const [audience, setAudience] = useState("production_ai_coding");
  const [profile, setProfile] = useState("text_core_v1");
  const [mode, setMode] = useState<Mode>("mock");
  const [postType, setPostType] = useState("original");
  const [parent, setParent] = useState("");
  const [quoted, setQuoted] = useState("");
  const [topic, setTopic] = useState("");
  const [media, setMedia] = useState("none");
  const [description, setDescription] = useState("");
  const [essential, setEssential] = useState(false);
  const [evidenceTime, setEvidenceTime] = useState(localTime());
  const [references, setReferences] = useState("[]");
  const [historical, setHistorical] = useState("");
  const [cutoff, setCutoff] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<Judgment[]>([]);
  const [limitations, setLimitations] = useState<string[]>([]);
  const persona = config.audiences.find((a) => a.audience_id === audience);
  function request(content: string): JudgeRequest {
    const available = new Date(evidenceTime).toISOString();
    const evidence = (value: string) =>
      value.trim()
        ? {
            text: value,
            occurred_at: available,
            available_at: available,
            provenance: "manual_description",
          }
        : null;
    const parsedReferences: unknown = JSON.parse(references);
    if (!Array.isArray(parsedReferences))
      throw new Error("References must be a JSON array.");
    const when = cutoff ? new Date(cutoff).toISOString() : undefined;
    return {
      candidate: {
        text: content,
        post_type: postType,
        provenance: "manual_ui",
        ...(when ? { content_available_at: when } : {}),
        parent: evidence(parent),
        quoted: evidence(quoted),
        media: { kind: media, essential, description: evidence(description) },
      },
      context: {
        ...(when ? { prediction_cutoff: when } : {}),
        topic: evidence(topic),
        references: parsedReferences,
        ...(historical.trim() ? { historical: JSON.parse(historical) } : {}),
      },
      audience_id: audience,
      profile_id: profile,
      execution_mode: mode,
    };
  }
  async function chooseReferences() {
    setError("");
    setBusy(true);
    try {
      const selected = await api<unknown[]>("/references", request(text));
      setReferences(JSON.stringify(selected, null, 2));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function run(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setResults([]);
    setLimitations([]);
    try {
      if (comparison) {
        const output = await api<{
          results: Judgment[];
          comparable: boolean;
          limitations: string[];
        }>("/compare", { requests: [request(text), request(variant)] });
        setResults(output.results);
        setLimitations([
          ...output.limitations,
          ...(!output.comparable
            ? ["These judgments do not meet the comparison requirements."]
            : []),
        ]);
      } else {
        setResults([await api<Judgment>("/judge", request(text))]);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <header className="workspace-header">
        <div>
          <span className="eyebrow">01 / The editorial workbench</span>
          <h1>A better question before you post.</h1>
          <p>
            Judge a candidate for a specific audience. Inspect the reasons, then
            test them against evidence.
          </p>
        </div>
        <Badge>Local-first · private by default</Badge>
      </header>
      <div className={`judge-layout ${comparison ? "comparison-layout" : ""}`}>
        <section className="panel composer">
          <div className="panel-heading">
            <h2>
              {comparison ? "Compare your variants" : "Start with a candidate"}
            </h2>
            <button
              className="text-button"
              onClick={() => {
                setComparison(!comparison);
                setResults([]);
              }}
            >
              {comparison ? "Single candidate" : "Compare variants →"}
            </button>
          </div>
          <form onSubmit={run}>
            <label>
              Candidate {comparison ? "A" : ""}
              <textarea
                className="tweet-input"
                placeholder="Paste the tweet you’re considering…"
                required
                maxLength={16000}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            <div className="input-meta">
              <span>Text is treated as content, never instructions.</span>
              <span>{text.length.toLocaleString()} characters</span>
            </div>
            {comparison && (
              <label>
                Candidate B
                <textarea
                  className="tweet-input"
                  placeholder="Paste an alternative you wrote…"
                  required
                  maxLength={16000}
                  value={variant}
                  onChange={(e) => setVariant(e.target.value)}
                />
              </label>
            )}
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
            {persona && (
              <details className="persona">
                <summary>About this audience · v{persona.version}</summary>
                <p>{persona.description}</p>
                <p>
                  <strong>Interests:</strong> {persona.interests.join(", ")}
                </p>
                <p>
                  <strong>Assumed knowledge:</strong>{" "}
                  {persona.assumed_knowledge.join(", ")}
                </p>
                <ul>
                  {persona.examples.map((example) => (
                    <li key={example}>{example}</li>
                  ))}
                </ul>
                <p className="muted">{persona.assumptions.join(" ")}</p>
              </details>
            )}
            <details className="context-form">
              <summary>Context, media &amp; prediction-time evidence</summary>
              <p className="muted">
                Jev is text-only. Add manual descriptions with honest
                availability times. Future information is rejected by the
                server.
              </p>
              <div className="two-cols">
                <label>
                  Post type
                  <select
                    value={postType}
                    onChange={(e) => setPostType(e.target.value)}
                  >
                    <option value="original">Original</option>
                    <option value="reply">Reply</option>
                    <option value="quote">Quote</option>
                    <option value="thread">Thread</option>
                  </select>
                </label>
                <label>
                  Evidence available at (local time)
                  <input
                    type="datetime-local"
                    value={evidenceTime}
                    onChange={(e) => setEvidenceTime(e.target.value)}
                    required
                  />
                </label>
              </div>
              <label>
                Parent post context
                <textarea
                  value={parent}
                  onChange={(e) => setParent(e.target.value)}
                  placeholder="Required context for a reply, if available"
                />
              </label>
              <label>
                Quoted post context
                <textarea
                  value={quoted}
                  onChange={(e) => setQuoted(e.target.value)}
                />
              </label>
              <label>
                Topic context
                <textarea
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder="Relevant background known at prediction time"
                />
              </label>
              <div className="two-cols">
                <label>
                  Media kind
                  <select
                    value={media}
                    onChange={(e) => setMedia(e.target.value)}
                  >
                    {["none", "image", "video", "audio", "other"].map(
                      (value) => (
                        <option key={value}>{value}</option>
                      ),
                    )}
                  </select>
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={essential}
                    onChange={(e) => setEssential(e.target.checked)}
                  />
                  Media is essential to understand this post
                </label>
              </div>
              {media !== "none" && (
                <label>
                  Manual media description / transcript
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Describe only what is present. Jev will receive this text, not the media."
                  />
                </label>
              )}
              <label>
                Prediction cutoff (optional; local time)
                <input
                  type="datetime-local"
                  value={cutoff}
                  onChange={(e) => setCutoff(e.target.value)}
                />
                <small>
                  Blank uses server time. Historical drafts are marked available
                  at the supplied cutoff.
                </small>
              </label>
              <button
                type="button"
                className="secondary small"
                disabled={busy || !text.trim()}
                onClick={chooseReferences}
              >
                Select eligible local references
              </button>
              <p className="fine-print">
                Keyword selection excludes target duplicates, threads, future
                evidence, and known final-test rows. Review the selected text
                before judging.
              </p>
              <label>
                Reference set (JSON)
                <textarea
                  className="code-input"
                  value={references}
                  onChange={(e) => setReferences(e.target.value)}
                  spellCheck={false}
                />
                <small>
                  Each reference needs candidate_id, text, published_at,
                  available_at, and split. Use timezone-aware ISO dates; do not
                  include outcomes.
                </small>
              </label>
              <label>
                Historical metadata (optional JSON)
                <textarea
                  className="code-input"
                  value={historical}
                  onChange={(e) => setHistorical(e.target.value)}
                  placeholder={
                    '{"followers": 1200, "observed_at": "…", "available_at": "…", "provenance": "as-of snapshot"}'
                  }
                  spellCheck={false}
                />
              </label>
            </details>
            {profile === "reference_enriched_v1" && (
              <p className="notice">
                The enriched profile requires an adequate relevant reference
                set. Missing evidence will produce no complete rating.
              </p>
            )}
            <div className="run-action">
              <button className="primary" disabled={busy}>
                {busy
                  ? "Judging…"
                  : comparison
                    ? "Compare variants"
                    : "Judge candidate"}{" "}
                <span aria-hidden="true">↗</span>
              </button>
              <small>
                {mode === "mock"
                  ? "Deterministic mock answers · no API spend"
                  : "Live TypeSafe request · configured budget applies"}
              </small>
            </div>
          </form>
          <ErrorBox error={error} />
        </section>
        <section className="result-space" aria-live="polite" aria-busy={busy}>
          {busy ? (
            <div className="empty">
              <span className="loader" />
              <h3>Gathering independent judgments</h3>
              <p>
                The result will separate editorial quality, answer certainty,
                and missing evidence.
              </p>
            </div>
          ) : results.length ? (
            <>
              {limitations.length > 0 && (
                <div className="notice">
                  <ul>
                    {limitations.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                </div>
              )}
              {comparison && (
                <p className="notice">
                  Both variants use the same supplied audience and context. A
                  higher editorial score does not establish a causal improvement
                  in reach.
                </p>
              )}
              <div className={comparison ? "compare-results" : ""}>
                {results.map((result, index) => (
                  <div key={result.judgment_id}>
                    {comparison && <h2>Candidate {index === 0 ? "A" : "B"}</h2>}
                    <Result
                      judgment={result}
                      config={config}
                      forecastAvailable={session.forecast.available}
                    />
                  </div>
                ))}
              </div>
            </>
          ) : (
            <Empty title="Your judgment will appear here">
              A transparent 1–5 editorial rating, the full factor evidence, and
              every limitation. No forecast is implied.
            </Empty>
          )}
        </section>
      </div>
      <details className="rubric-bottom">
        <summary>Inspect the versioned rubric and operating limits</summary>
        <JsonView value={config.rubric} label="Rubric configuration" />
        <JsonView value={config.limits} label="Runtime limits" />
      </details>
    </>
  );
}
