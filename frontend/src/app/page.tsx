"use client";

import { ChangeEvent, useEffect, useMemo, useState } from "react";
import type {
  AnalysisResult,
  ClaimEffect,
  ClaimResult,
  ClaimStatus,
} from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type VideoKey = "base" | "branch_a" | "branch_b";

const uploadMeta: Record<VideoKey, { eyebrow: string; title: string; help: string }> = {
  base: {
    eyebrow: "SOURCE 01",
    title: "BASE",
    help: "The original, unedited video",
  },
  branch_a: {
    eyebrow: "CUT 02",
    title: "BRANCH A",
    help: "The first independent edit",
  },
  branch_b: {
    eyebrow: "CUT 03",
    title: "BRANCH B",
    help: "The second independent edit",
  },
};

const stages = [
  "Preprocessing video and speech",
  "Aligning both cuts to BASE",
  "Composing the combined timeline",
  "Checking claim preservation with M3",
];

function formatTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - minutes * 60;
  return `${minutes}:${remainder.toFixed(1).padStart(4, "0")}`;
}

function statusLabel(status: ClaimStatus) {
  return status === "preserved" ? "Preserved" : status === "degraded" ? "Degraded" : "Broken";
}

function UploadCard({
  kind,
  file,
  onChange,
}: {
  kind: VideoKey;
  file?: File;
  onChange: (file?: File) => void;
}) {
  const [preview, setPreview] = useState<string>();
  const meta = uploadMeta[kind];

  useEffect(() => {
    if (!file) {
      setPreview(undefined);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function selectFile(event: ChangeEvent<HTMLInputElement>) {
    onChange(event.target.files?.[0]);
  }

  return (
    <label className={`upload-card ${file ? "upload-card--ready" : ""}`}>
      <input type="file" accept="video/mp4,.mp4" onChange={selectFile} />
      <div className="upload-heading">
        <div>
          <span className="eyebrow">{meta.eyebrow}</span>
          <h2>{meta.title}</h2>
        </div>
        <span className="file-state">{file ? "READY" : "SELECT"}</span>
      </div>
      {preview ? (
        <video className="video-preview" src={preview} controls preload="metadata" />
      ) : (
        <div className="upload-empty">
          <span className="upload-plus">+</span>
          <span>Choose MP4</span>
        </div>
      )}
      <div className="file-meta">
        <span>{file?.name ?? meta.help}</span>
        {file && <span>{(file.size / 1024 / 1024).toFixed(1)} MB</span>}
      </div>
    </label>
  );
}

function Effect({ label, effect }: { label: string; effect: ClaimEffect }) {
  return (
    <section className="effect">
      <div className="effect-title">
        <span>{label}</span>
        <strong className={`status status--${effect.status}`}>{statusLabel(effect.status)}</strong>
      </div>
      <p>{effect.rationale}</p>
      {effect.evidence.length > 0 && (
        <div className="evidence-list">
          {effect.evidence.map((item, index) => (
            <div className="evidence" key={`${item.start}-${item.end}-${index}`}>
              <time>
                {formatTime(item.start)}–{formatTime(item.end)}
              </time>
              <span>{item.description}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ClaimCard({ claim }: { claim: ClaimResult }) {
  const isConflict = claim.interaction !== "none";
  return (
    <article className={`claim-card ${isConflict ? "claim-card--conflict" : ""}`}>
      <div className="claim-header">
        <div>
          <span className="eyebrow">
            {claim.claim_type.replaceAll("_", " ")} · {claim.importance}
          </span>
          <h3>{claim.claim}</h3>
        </div>
        <span className={`interaction-pill ${isConflict ? "interaction-pill--conflict" : ""}`}>
          {isConflict ? "NEW CONFLICT" : "NO NEW CONFLICT"}
        </span>
      </div>
      {claim.explanation && <p className="claim-explanation">{claim.explanation}</p>}
      {claim.base_evidence.length > 0 && (
        <div className="base-evidence">
          <strong>BASE evidence</strong>
          {claim.base_evidence.map((item, index) => (
            <span key={`${item.start}-${item.end}-${index}`}>
              {formatTime(item.start)}–{formatTime(item.end)} · {item.description}
            </span>
          ))}
        </div>
      )}
      <div className="effects-grid">
        <Effect label="Branch A" effect={claim.branch_a} />
        <Effect label="Branch B" effect={claim.branch_b} />
        <Effect label="Combined" effect={claim.combined} />
      </div>
      <details>
        <summary>Deterministic decision</summary>
        <p>{claim.deterministic_rule}</p>
      </details>
    </article>
  );
}

function Results({ result }: { result: AnalysisResult }) {
  const orderedClaims = useMemo(
    () => [...result.claims].sort((a, b) => Number(b.interaction !== "none") - Number(a.interaction !== "none")),
    [result.claims],
  );
  return (
    <section className={`results ${result.conflict_detected ? "results--conflict" : "results--safe"}`}>
      <div className="result-summary">
        <span className="result-icon">{result.conflict_detected ? "!" : "✓"}</span>
        <div>
          <span className="eyebrow">ANALYSIS COMPLETE</span>
          <h2>{result.conflict_detected ? "Semantic conflict detected" : "No new semantic conflict"}</h2>
          <p>{result.summary}</p>
        </div>
        <div className="confidence">
          <strong>{Math.round(result.overall_confidence * 100)}%</strong>
          <span>M3 confidence</span>
        </div>
      </div>
      <div className="claims">
        {orderedClaims.map((claim) => (
          <ClaimCard claim={claim} key={claim.claim_id} />
        ))}
      </div>
      {result.combined_timeline.length > 0 && (
        <details className="timeline">
          <summary>Analyzed Combined representation</summary>
          <div className="timeline-list">
            {result.combined_timeline.map((slice) => (
              <div className="timeline-row" key={slice.base_index}>
                <time>{formatTime(slice.start)}–{formatTime(slice.end)}</time>
                <strong>{slice.verdict}</strong>
                <span>{slice.text || "No surviving content"}</span>
              </div>
            ))}
          </div>
        </details>
      )}
      <p className="model-note">Reasoning: {result.model} through {result.provider}</p>
    </section>
  );
}

export default function Home() {
  const [files, setFiles] = useState<Partial<Record<VideoKey, File>>>({});
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState(0);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<AnalysisResult>();
  const ready = Boolean(files.base && files.branch_a && files.branch_b);

  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => setStage((value) => Math.min(value + 1, stages.length - 1)), 5500);
    return () => window.clearInterval(timer);
  }, [loading]);

  async function analyze() {
    if (!files.base || !files.branch_a || !files.branch_b) return;
    setLoading(true);
    setStage(0);
    setError(undefined);
    setResult(undefined);
    const body = new FormData();
    body.append("base", files.base);
    body.append("branch_a", files.branch_a);
    body.append("branch_b", files.branch_b);
    try {
      const response = await fetch(`${API_URL}/api/analyze`, { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Analysis failed.");
      setResult(payload as AnalysisResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not reach the MergeCut API.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="MergeCut home">
          <span className="brand-mark">M</span>
          <span>MERGECUT</span>
        </a>
        <span className="header-note">Semantic merge check · M3 / GMI Cloud</span>
      </header>

      <section className="hero" id="top">
        <span className="eyebrow hero-kicker">VIDEO VERSION CONTROL, WITH CONTEXT</span>
        <h1>Catch the conflicts<br />your timeline can’t see.</h1>
        <p>
          Compare an original video with two independent edits. MergeCut finds when both cuts look safe alone—but change the meaning together.
        </p>
      </section>

      <section className="workspace" aria-label="Video uploads">
        {(Object.keys(uploadMeta) as VideoKey[]).map((kind) => (
          <UploadCard
            kind={kind}
            file={files[kind]}
            key={kind}
            onChange={(file) => setFiles((current) => ({ ...current, [kind]: file }))}
          />
        ))}
      </section>

      <section className="action-bar">
        <div className="action-copy">
          <span className={`ready-dot ${ready ? "ready-dot--active" : ""}`} />
          {ready ? "Three videos ready" : "Select BASE, Branch A, and Branch B"}
        </div>
        <button disabled={!ready || loading} onClick={analyze}>
          {loading ? "Analyzing…" : "Analyze merge"}
          <span>→</span>
        </button>
      </section>

      {loading && (
        <section className="progress" aria-live="polite">
          <div className="progress-line"><span style={{ width: `${((stage + 1) / stages.length) * 100}%` }} /></div>
          <span>{String(stage + 1).padStart(2, "0")} / 04</span>
          <strong>{stages[stage]}</strong>
          <p>Longer videos may take a few minutes. Keep this page open.</p>
        </section>
      )}
      {error && <div className="error" role="alert"><strong>Analysis stopped.</strong> {error}</div>}
      {result && <Results result={result} />}

      <footer>
        <span>Rendered-content analysis for same-source video edits.</span>
        <span>Controlled MVP · English · shot-level</span>
      </footer>
    </main>
  );
}
