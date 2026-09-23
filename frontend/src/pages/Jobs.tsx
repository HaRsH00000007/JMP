import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import { Card, Empty, ErrorBox, ProgressBar, STAGE_LABEL, StatusPill, fmtDate } from "../components/ui";

export default function Jobs() {
  const [tab, setTab] = useState<"individual" | "bulk">("individual");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const jobs = useQuery({
    queryKey: ["jobs", status, page],
    queryFn: () => api.jobs({ status: status || undefined, kind: "individual", page, size: 25 }),
    refetchInterval: 5000,
    enabled: tab === "individual",
  });
  const bulk = useQuery({ queryKey: ["bulk-list"], queryFn: () => api.bulkJobs(), refetchInterval: 5000, enabled: tab === "bulk" });

  return (
    <>
      <div className="page-head"><div><h1>Jobs</h1><p className="lead">Every generation runs as a background job. This list refreshes automatically.</p></div></div>
      <div className="tabs">
        <button type="button" className={`tab ${tab === "individual" ? "on" : ""}`} onClick={() => setTab("individual")}>Individual</button>
        <button type="button" className={`tab ${tab === "bulk" ? "on" : ""}`} onClick={() => setTab("bulk")}>Bulk</button>
      </div>
      {tab === "individual" ? (
        <Card
          title="Individual jobs"
          actions={
            <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Filter by status">
              <option value="">All statuses</option>
              {["queued", "processing", "completed", "failed", "cancelled"].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          }
        >
          <ErrorBox error={jobs.error} />
          {jobs.data && jobs.data.items.length === 0 && <Empty>No jobs.</Empty>}
          {jobs.data && jobs.data.items.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Journey</th><th>Route</th><th>Status</th><th>Stage</th><th>Queued</th><th>Result</th></tr></thead>
                <tbody>
                  {jobs.data.items.map((j) => (
                    <tr key={j.job_id}>
                      <td className="nowrap"><Link to={`/individual?job=${j.job_id}`}>{j.journey_code ?? j.job_id.slice(0, 8)}</Link></td>
                      <td className="small">{j.route.join(" → ")}</td>
                      <td><StatusPill status={j.status} /></td>
                      <td className="small">{STAGE_LABEL[j.stage] ?? j.stage}{j.attempt > 1 ? ` (attempt ${j.attempt})` : ""}</td>
                      <td className="nowrap small">{fmtDate(j.queued_at)}</td>
                      <td>
                        {j.document_id && (
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => downloadFile(`/documents/${j.document_id}/download`, `${j.journey_code}.pdf`)}>PDF</button>
                        )}
                        {j.error && <span className="small"><code>{j.error.code}</code></span>}
                        {j.status === "failed" && (
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => api.retryJob(j.job_id).then(() => jobs.refetch())}>Retry</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {jobs.data && jobs.data.total > 25 && (
            <div className="row" style={{ marginTop: 12 }}>
              <button type="button" className="btn btn-ghost btn-sm" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button>
              <span className="small muted">Page {page} of {Math.ceil(jobs.data.total / 25)}</span>
              <button type="button" className="btn btn-ghost btn-sm" disabled={page * 25 >= jobs.data.total} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          )}
        </Card>
      ) : (
        <Card title="Bulk jobs">
          <ErrorBox error={bulk.error} />
          {bulk.data && bulk.data.items.length === 0 && <Empty>No bulk jobs.</Empty>}
          {bulk.data && bulk.data.items.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>File</th><th>Status</th><th>Progress</th><th>Rows</th><th>OK</th><th>Failed</th><th>Created</th><th /></tr></thead>
                <tbody>
                  {bulk.data.items.map((b) => (
                    <tr key={b.job_id}>
                      <td>{b.filename}</td><td><StatusPill status={b.status} /></td>
                      <td style={{ minWidth: 130 }}><ProgressBar value={b.percentage} /></td>
                      <td>{b.total}</td><td>{b.successful}</td><td>{b.failed + b.invalid}</td>
                      <td className="nowrap small">{fmtDate(b.created_at)}</td>
                      <td><Link className="btn btn-ghost btn-sm" to={`/bulk?bulk=${b.job_id}`}>Open</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
