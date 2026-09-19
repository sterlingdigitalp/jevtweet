import { useState } from "react";
import { api } from "./api";
import { ErrorBox, JsonView } from "./components";
import type { Report } from "./types";
export default function Discovery({ experimentId }: { experimentId: string }) {
  const [proposals, setProposals] = useState("[]");
  const [cost, setCost] = useState("0");
  const [rows, setRows] = useState("50");
  const [requests, setRequests] = useState("50");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Report | null>(null);
  const [errors, setErrors] = useState<unknown>(null);
  async function inspect() {
    setBusy(true);
    setError("");
    try {
      setErrors(
        await api(`/discovery/${encodeURIComponent(experimentId)}/errors`),
      );
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
    try {
      const parsed: unknown = JSON.parse(proposals);
      if (!Array.isArray(parsed) || !parsed.length)
        throw new Error("Add at least one reviewed feature proposal.");
      setResult(
        await api<Report>("/discovery", {
          experiment_id: experimentId,
          proposals: parsed,
          cost_limit_usd: Number(cost),
          max_rows: Number(rows),
          max_requests: Number(requests),
          live: false,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel discovery-note">
      <h2>Bounded feature discovery</h2>
      <p>
        Reviewed proposals are evaluated against development-set errors. The
        final test never enters the discovery loop, and features cannot promote
        themselves to production.
      </p>
      {!experimentId ? (
        <p className="muted">
          Run or open an experiment to inspect development errors and submit a
          reviewed proposal.
        </p>
      ) : (
        <details>
          <summary>
            Explore proposals for this experiment · mock computation
          </summary>
          <p className="fine-print">
            This interface uses deterministic mock feature computation. Live
            discovery requires the documented CLI with explicit spending
            controls. Maximum iterations, active features, and proposal count
            are enforced server-side.
          </p>
          <button className="secondary small" disabled={busy} onClick={inspect}>
            Inspect development errors
          </button>
          {errors != null && (
            <JsonView value={errors} label="Development errors only" open />
          )}
          <form onSubmit={run}>
            <label>
              Reviewed proposals (JSON array)
              <textarea
                className="code-input"
                rows={6}
                value={proposals}
                onChange={(e) => setProposals(e.target.value)}
                spellCheck={false}
              />
              <small>
                Each proposal needs feature_id, version, type, instructions,
                criteria for a score, reviewed_by, reviewed_at, and hypothesis.
              </small>
            </label>
            <div className="discovery-caps">
              <label>
                Cost ceiling (USD)
                <input
                  required
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  value={cost}
                  onChange={(e) => setCost(e.target.value)}
                />
              </label>
              <label>
                Maximum rows
                <input
                  required
                  type="number"
                  min="1"
                  max="1000"
                  value={rows}
                  onChange={(e) => setRows(e.target.value)}
                />
              </label>
              <label>
                Maximum requests
                <input
                  required
                  type="number"
                  min="1"
                  max="1000"
                  value={requests}
                  onChange={(e) => setRequests(e.target.value)}
                />
              </label>
            </div>
            <button className="secondary" disabled={busy}>
              {busy ? "Processing…" : "Evaluate reviewed mock proposals"}
            </button>
          </form>
          <ErrorBox error={error} />
          {result && (
            <JsonView
              value={result}
              label="Discovery audit, decisions, and validation"
              open
            />
          )}
        </details>
      )}
    </section>
  );
}
