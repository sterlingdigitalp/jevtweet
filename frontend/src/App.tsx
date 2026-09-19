import { useEffect, useState } from "react";
import { api, setCsrfToken } from "./api";
import { ErrorBox, JsonView } from "./components";
import Judge from "./Judge";
import Corpus from "./Corpus";
import Experiments from "./Experiments";
import type { Config, Session } from "./types";
type Workspace = "judge" | "corpus" | "experiments";
export default function App() {
  const [workspace, setWorkspace] = useState<Workspace>(() =>
    ["judge", "corpus", "experiments"].includes(location.hash.slice(1))
      ? (location.hash.slice(1) as Workspace)
      : "judge",
  );
  const [session, setSession] = useState<Session | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    Promise.all([api<Session>("/session"), api<Config>("/config")])
      .then(([s, c]) => {
        if (active) {
          setCsrfToken(s.csrf_token);
          setSession(s);
          setConfig(c);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  useEffect(() => {
    const listener = () => {
      const value = location.hash.slice(1);
      if (["judge", "corpus", "experiments"].includes(value))
        setWorkspace(value as Workspace);
    };
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, []);
  function navigate(next: Workspace) {
    setWorkspace(next);
    location.hash = next;
    document.getElementById("workspace")?.focus();
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">
        Skip to workspace
      </a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#judge"
          onClick={() => navigate("judge")}
          aria-label="JevTweet home"
        >
          <span className="brand-mark">
            j<span>↗</span>
          </span>
          <span>
            JevTweet<small>THE EDITORIAL LAB</small>
          </span>
        </a>
        <nav aria-label="Workspaces">
          {(
            [
              {
                id: "judge",
                number: "01",
                label: "Judge",
                caption: "Before you post",
              },
              {
                id: "corpus",
                number: "02",
                label: "Corpus",
                caption: "Build the evidence",
              },
              {
                id: "experiments",
                number: "03",
                label: "Experiments",
                caption: "Test the hypothesis",
              },
            ] as const
          ).map((item) => (
            <button
              key={item.id}
              className={`nav-item ${workspace === item.id ? "active" : ""}`}
              aria-current={workspace === item.id ? "page" : undefined}
              onClick={() => navigate(item.id)}
            >
              <span className="nav-number">{item.number}</span>
              <span>
                {item.label}
                <small>{item.caption}</small>
              </span>
              <span className="nav-arrow" aria-hidden="true">
                ↗
              </span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="status-dot" /> Local workspace
          <p>
            Built around Jev.
            <br />
            Grounded in evidence.
          </p>
          <small>V1 · Editorial mode</small>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>Independent judgments. Inspectable decisions.</span>
          <div>
            <span
              className={`status-dot ${session?.live_configured ? "" : "muted-dot"}`}
            />
            {session
              ? session.live_configured
                ? "Live connection configured"
                : "Offline / mock available"
              : "Connecting to local API…"}
          </div>
        </header>
        <main id="workspace" tabIndex={-1}>
          <ErrorBox error={error} />
          {error && (
            <div className="connection-help">
              <p>
                Start the local backend and reload. The interface requires the
                real application API.
              </p>
              <button
                className="secondary"
                onClick={() => setAttempt((n) => n + 1)}
              >
                Retry connection
              </button>
            </div>
          )}
          {config && session ? (
            workspace === "judge" ? (
              <Judge config={config} session={session} />
            ) : workspace === "corpus" ? (
              <Corpus config={config} session={session} />
            ) : (
              <Experiments session={session} />
            )
          ) : !error ? (
            <div className="empty">
              <span className="loader" />
              <h1>Opening your local lab</h1>
              <p>Loading the versioned rubric and session controls.</p>
            </div>
          ) : null}
        </main>
        <footer className="app-footer">
          <span>Editorial potential ≠ calibrated breakout probability</span>
          {session && (
            <details>
              <summary>Session, spend &amp; forecast status</summary>
              <JsonView
                value={{
                  live_configured: session.live_configured,
                  spending: session.spending,
                  forecast: session.forecast,
                }}
                label="Public session details"
                open
              />
            </details>
          )}
        </footer>
      </div>
    </div>
  );
}
