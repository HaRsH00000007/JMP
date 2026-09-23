import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, downloadFile, type CsvValidation } from "../api/client";
import { Card, Empty, ErrorBox, ProgressBar, Stat, StatusPill, fmtDate, fmtDuration, fmtMoney } from "../components/ui";

export default function BulkJmp() {
  const [params, setParams] = useSearchParams();
  const bulkId = params.get("bulk");
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [mode, setMode] = useState("realtime");
  const input = useRef<HTMLInputElement>(null);

  const validate = useMutation({ mutationFn: (f: File) => api.validateCsv(f) });
  const start = useMutation({
    mutationFn: (v: CsvValidation) => api.createBulk(v.upload_id, mode),
    onSuccess: (r) => {
      setParams({ bulk: r.job_id });
      validate.reset();
      setFile(null);
    },
  });

  const pick = (f: File | undefined | null) => {
    if (!f) return;
    setFile(f);
    validate.mutate(f);
  };
  const v = validate.data;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Bulk JMP</h1>
          <p className="lead">Upload a CSV with one journey per row. Rows are validated first; valid rows are processed in the background, each independently.</p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={() => downloadFile("/bulk-jobs/sample.csv", "jmp_bulk_template.csv")}>Download CSV template</button>
      </div>

      {bulkId && <BulkProgress bulkId={bulkId} onClose={() => setParams({})} />}

      <Card title="1 · Upload & validate">
        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files?.[0]); }}
          role="button"
          tabIndex={0}
        >
          <strong>{file ? file.name : "Drop a CSV here or click to choose"}</strong>
          <div className="small muted">Columns: route_id, start_location, stop_1 … stop_13, end_location, optional vehicle_type, travel_date, depart_time, manager_name, emergency_contact, nearest_hospital, nearest_police</div>
          <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={(e) => pick(e.target.files?.[0])} />
        </div>
        {validate.isPending && <p className="muted">Validating…</p>}
        <ErrorBox error={validate.error} />
      </Card>

      {v && (
        <Card title="2 · Review" actions={<StatusPill status={v.invalid_rows ? "completed_with_errors" : "completed"} />}>
          <div className="stats">
            <Stat label="Rows" value={v.total_rows} />
            <Stat label="Valid" value={v.valid_rows} />
            <Stat label="Invalid" value={v.invalid_rows} sub={v.invalid_rows ? "will be listed in the manifest, not processed" : undefined} />
          </div>
          {v.errors.length > 0 && (
            <>
              <h2 style={{ marginBottom: 8 }}>Validation errors</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Row</th><th>Route ID</th><th>Field</th><th>Issue</th></tr></thead>
                  <tbody>
                    {v.errors.slice(0, 100).map((e, i) => (
                      <tr key={i}><td>{e.row}</td><td>{e.route_id}</td><td><code>{e.field}</code></td><td>{e.issue}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <h2 style={{ margin: "16px 0 8px" }}>Preview (first {v.preview.length} rows)</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Row</th><th>Route ID</th><th>Journey</th><th>Status</th></tr></thead>
              <tbody>
                {v.preview.map((p) => (
                  <tr key={p.row}>
                    <td>{p.row}</td><td>{p.route_id}</td>
                    <td>{[p.start, ...p.stops, p.end].filter(Boolean).join(" → ")}</td>
                    <td><StatusPill status={p.valid ? "valid" : "invalid"} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ marginTop: 16, flexWrap: "wrap" }}>
            <label className="field" style={{ marginBottom: 0 }}>Narrative mode
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="realtime">Realtime (prompt-cached, fastest)</option>
                <option value="batch">Message Batches (50% cheaper, up to 24 h)</option>
              </select>
            </label>
            <button type="button" className="btn btn-primary" disabled={!v.valid_rows || start.isPending} onClick={() => start.mutate(v)}>
              {start.isPending ? "Starting…" : `Start generation (${v.valid_rows} journeys)`}
            </button>
          </div>
          <ErrorBox error={start.error} />
        </Card>
      )}

      <RecentBulk onOpen={(id) => setParams({ bulk: id })} />
    </>
  );
}

function BulkProgress({ bulkId, onClose }: { bulkId: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["bulk", bulkId],
    queryFn: () => api.bulkJob(bulkId),
    refetchInterval: (s) => (s.state.data && !["queued", "processing", "finalizing"].includes(s.state.data.status) ? false : 3000),
  });
  const running = q.data && ["queued", "processing", "finalizing"].includes(q.data.status);
  const items = useQuery({
    queryKey: ["bulk-items", bulkId, q.data?.processed],
    queryFn: () => api.bulkItems(bulkId),
    enabled: !!q.data,
  });
  const b = q.data;
  const failed = (items.data?.items ?? []).filter((i) => ["failed", "invalid", "cancelled"].includes(i.status));
  return (
    <Card
      title={<>Bulk job · {b?.filename ?? bulkId.slice(0, 8)} {b && <StatusPill status={b.status} />}</>}
      actions={<>
        {running && <button type="button" className="btn btn-danger btn-sm" onClick={() => api.cancelBulk(bulkId).then(() => q.refetch())}>Cancel queued rows</button>}
        {b && !running && b.failed > 0 && <button type="button" className="btn btn-ghost btn-sm" onClick={() => api.retryFailed(bulkId).then(() => q.refetch())}>Retry failed rows</button>}
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
      </>}
    >
      <ErrorBox error={q.error} />
      {b && (
        <>
          <ProgressBar value={b.percentage} label="Bulk progress" />
          <div className="stats" style={{ marginTop: 14 }}>
            <Stat label="Total rows" value={b.total} sub={`${b.valid} valid · ${b.invalid} invalid`} />
            <Stat label="Processed" value={`${b.processed} / ${b.valid}`} sub={b.eta_seconds != null ? `ETA ${fmtDuration(b.eta_seconds)}` : fmtDuration(b.elapsed_seconds)} />
            <Stat label="Successful" value={b.successful} />
            <Stat label="Failed" value={b.failed} />
            <Stat label="LLM cost" value={fmtMoney(b.usage.estimated_cost_usd, "USD")} sub={fmtMoney(b.usage.estimated_cost_inr, "INR") + ` · ${b.llm_mode}`} />
          </div>
          {b.error && <div className="alert alert-bad">{b.error}</div>}
          {running && <p className="small muted">Processing continues on the server if you close this page.</p>}
          {b.downloads.zip && (
            <div className="row" style={{ flexWrap: "wrap" }}>
              <button type="button" className="btn btn-amber" onClick={() => downloadFile(b.downloads.zip!.replace("/api/v1", ""), `JMP_Batch_${bulkId}.zip`)}>Download ZIP</button>
              <button type="button" className="btn btn-ghost" onClick={() => downloadFile(`/bulk-jobs/${bulkId}/manifest?format=csv`, "manifest.csv")}>Manifest CSV</button>
              <button type="button" className="btn btn-ghost" onClick={() => downloadFile(`/bulk-jobs/${bulkId}/manifest?format=xlsx`, "manifest.xlsx")}>Manifest XLSX</button>
              <button type="button" className="btn btn-ghost" onClick={() => downloadFile(`/bulk-jobs/${bulkId}/manifest?format=json`, "summary.json")}>Summary JSON</button>
            </div>
          )}
          {failed.length > 0 && (
            <>
              <h2 style={{ margin: "16px 0 8px" }}>Failed / invalid rows ({failed.length})</h2>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Row</th><th>Route ID</th><th>Status</th><th>Error</th></tr></thead>
                  <tbody>
                    {failed.slice(0, 200).map((i) => (
                      <tr key={i.row_number}>
                        <td>{i.row_number}</td><td>{i.route_id}</td><td><StatusPill status={i.status} /></td>
                        <td><code>{i.error_code}</code> <span className="small">{i.error_message}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </Card>
  );
}

function RecentBulk({ onOpen }: { onOpen: (id: string) => void }) {
  const q = useQuery({ queryKey: ["bulk-list"], queryFn: () => api.bulkJobs(), refetchInterval: 10_000 });
  return (
    <Card title="Recent bulk jobs">
      {q.data && q.data.items.length === 0 && <Empty>No bulk jobs yet.</Empty>}
      {q.data && q.data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>File</th><th>Created</th><th>Status</th><th>Progress</th><th>OK / Failed / Invalid</th><th /></tr></thead>
            <tbody>
              {q.data.items.map((b) => (
                <tr key={b.job_id}>
                  <td>{b.filename}</td><td className="nowrap">{fmtDate(b.created_at)}</td><td><StatusPill status={b.status} /></td>
                  <td style={{ minWidth: 120 }}><ProgressBar value={b.percentage} /></td>
                  <td>{b.successful} / {b.failed} / {b.invalid}</td>
                  <td><button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpen(b.job_id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
