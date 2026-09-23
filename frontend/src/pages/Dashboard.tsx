import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Card, Empty, RiskPill, Stat, StatusPill, fmtDate, fmtMoney } from "../components/ui";

export default function Dashboard() {
  const docs = useQuery({ queryKey: ["documents", "recent"], queryFn: () => api.documents({ size: 6 }), refetchInterval: 15_000 });
  const running = useQuery({ queryKey: ["jobs", "processing"], queryFn: () => api.jobs({ status: "processing", size: 1 }), refetchInterval: 5000 });
  const failed = useQuery({ queryKey: ["jobs", "failed"], queryFn: () => api.jobs({ status: "failed", size: 1 }), refetchInterval: 15_000 });
  const bulk = useQuery({ queryKey: ["bulk-list"], queryFn: () => api.bulkJobs(), refetchInterval: 10_000 });
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.usage, refetchInterval: 30_000 });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 60_000 });

  const u = usage.data;
  const hit = u?.by_model.filter((m) => m.model !== "mock") ?? [];
  const cacheHit = hit.length
    ? hit.reduce((a, m) => a + m.cache_read_input_tokens, 0) /
      Math.max(1, hit.reduce((a, m) => a + m.cache_read_input_tokens + m.cache_creation_input_tokens + m.input_tokens, 0))
    : null;
  const activeBulk = bulk.data?.items.filter((b) => ["queued", "processing", "finalizing"].includes(b.status)) ?? [];

  return (
    <>
      <div className="page-head">
        <div><h1>Dashboard</h1><p className="lead">Journey Management Plans generated from route data, Danone's 25-hazard library and deterministic scoring.</p></div>
        <div className="row"><Link className="btn btn-primary" to="/individual">New JMP</Link><Link className="btn btn-ghost" to="/bulk">Bulk upload</Link></div>
      </div>
      <div className="stats">
        <Stat label="Reports generated" value={docs.data?.total ?? "—"} />
        <Stat label="Jobs running" value={running.data?.total ?? "—"} sub={`${activeBulk.length} bulk job(s) active`} />
        <Stat label="Failed jobs" value={failed.data?.total ?? "—"} sub={<Link to="/jobs">review</Link>} />
        <Stat label="Cost per JMP" value={u ? fmtMoney(u.actual_cost_per_jmp_usd, "USD") : "—"} sub={u ? `${fmtMoney(u.actual_cost_per_jmp_inr, "INR")} · per 1,000: ${fmtMoney(u.actual_cost_per_1000_jmp_usd, "USD")}` : undefined} />
        <Stat label="Prompt-cache hit" value={cacheHit == null ? "—" : `${Math.round(cacheHit * 100)}%`} sub="share of input tokens read from cache" />
      </div>
      <div className="grid-2">
        <Card title="Recent reports" actions={<Link to="/reports" className="small">All reports →</Link>}>
          {docs.data && docs.data.items.length === 0 && <Empty>No reports yet — generate one from Individual JMP.</Empty>}
          {docs.data && docs.data.items.length > 0 && (
            <table className="table">
              <tbody>
                {docs.data.items.map((d) => (
                  <tr key={d.document_id}>
                    <td className="nowrap"><b>{d.document_code}</b><div className="small muted">{fmtDate(d.created_at)}</div></td>
                    <td className="small">{d.route_name}</td>
                    <td><RiskPill level={d.risk_level} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Bulk jobs" actions={<Link to="/bulk" className="small">Bulk JMP →</Link>}>
          {bulk.data && bulk.data.items.length === 0 && <Empty>No bulk jobs yet.</Empty>}
          {bulk.data && bulk.data.items.length > 0 && (
            <table className="table">
              <tbody>
                {bulk.data.items.slice(0, 6).map((b) => (
                  <tr key={b.job_id}>
                    <td><Link to={`/bulk?bulk=${b.job_id}`}>{b.filename}</Link><div className="small muted">{fmtDate(b.created_at)}</div></td>
                    <td className="small">{b.successful}/{b.valid} OK</td>
                    <td><StatusPill status={b.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
      {settings.data && (
        <Card title="Configuration">
          <dl className="kv">
            <dt>Narrative</dt><dd>{settings.data.llm.provider === "mock" ? "Template writer (demo — no Claude key)" : `${settings.data.llm.model} · effort ${settings.data.llm.effort} · cache TTL ${settings.data.llm.cache_ttl}`}</dd>
            <dt>Route data</dt><dd>{Object.entries(settings.data.providers).map(([k, v]) => `${k}: ${v}`).join(" · ")}</dd>
            <dt>Risk band shown</dt><dd>{settings.data.report.display_band_method === "severity_band" ? "Severity band (master PDF method) — matrix zone shown alongside" : "Danone likelihood × consequence matrix"}</dd>
            <dt>Execution</dt><dd>{settings.data.execution} · LLM concurrency {settings.data.llm.concurrency} · bulk mode {settings.data.llm.bulk_mode}</dd>
          </dl>
        </Card>
      )}
    </>
  );
}
