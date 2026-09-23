import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function StatusPill({ status }: { status: string }) {
  const s = status.toLowerCase();
  const tone =
    s === "completed" || s === "succeeded" || s === "success"
      ? "ok"
      : s === "failed" || s === "invalid"
        ? "bad"
        : s === "completed_with_errors"
          ? "warn"
          : s === "cancelled"
            ? "muted"
            : "info";
  return <span className={`pill pill-${tone}`}>{status.replace(/_/g, " ")}</span>;
}

export function RiskPill({ level }: { level: string | null }) {
  if (!level) return <span className="muted">—</span>;
  const tone = level === "HIGH" ? "bad" : level === "LOW" ? "ok" : "warn";
  return <span className={`pill pill-${tone}`}>{level}</span>;
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className="progress" role="progressbar" aria-valuenow={v} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className="progress-bar" style={{ width: `${v}%` }} />
      <span className="progress-label">{v}%</span>
    </div>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  const details = Array.isArray(e.details) ? (e.details as { field?: string; issue?: string }[]) : [];
  return (
    <div className="alert alert-bad" role="alert">
      <strong>{e.code ?? "Error"}</strong> — {e.message ?? String(error)}
      {details.length > 0 && (
        <ul>
          {details.slice(0, 10).map((d, i) => (
            <li key={i}>
              {d.field ? <code>{d.field}</code> : null} {d.issue}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m ? `${m}m ${s}s` : `${s}s`;
}

export function fmtMoney(v: number, currency: "USD" | "INR"): string {
  return new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: v < 1 ? 4 : 2,
  }).format(v);
}

export const STAGES = ["queued", "analyse", "narrative", "render", "done"] as const;
export const STAGE_LABEL: Record<string, string> = {
  queued: "Queued",
  analyse: "Route & hazard analysis",
  narrative: "Narrative (Claude)",
  awaiting_batch: "Waiting for batch",
  render: "Rendering PDF",
  done: "Done",
};

export function StageTrack({ stage, status }: { stage: string; status: string }) {
  const idx = stage === "awaiting_batch" ? 2 : STAGES.indexOf(stage as (typeof STAGES)[number]);
  return (
    <ol className="stages">
      {STAGES.map((s, i) => {
        const state = status === "completed" || i < idx ? "done" : i === idx ? (status === "failed" ? "failed" : "active") : "todo";
        return (
          <li key={s} className={`stage stage-${state}`}>
            <span className="dot" />
            {STAGE_LABEL[s]}
          </li>
        );
      })}
    </ol>
  );
}
