import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api, downloadFile, openPdf } from "../api/client";
import { Card, Empty, ErrorBox, RiskPill, StatusPill, fmtDate, fmtMoney } from "../components/ui";

export default function Reports() {
  const [q, setQ] = useState("");
  const [risk, setRisk] = useState("");
  const [region, setRegion] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const qc = useQueryClient();
  const docs = useQuery({
    queryKey: ["documents", q, risk, region, from, to, page],
    queryFn: () => api.documents({ q, risk, region, from, to, page, size: 25 }),
  });

  const remove = async (id: string) => {
    await api.deleteDocument(id);
    setConfirmDelete(null);
    if (selected === id) setSelected(null);
    qc.invalidateQueries({ queryKey: ["documents"] });
  };

  return (
    <>
      <div className="page-head"><div><h1>Generated Reports</h1><p className="lead">Search, view and download generated Journey Management Plans.</p></div></div>
      <Card>
        <div className="filters">
          <label>Search<input type="search" placeholder="Journey ID, place…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></label>
          <label>Risk
            <select value={risk} onChange={(e) => { setRisk(e.target.value); setPage(1); }}>
              <option value="">Any</option><option>LOW</option><option>MODERATE</option><option>HIGH</option>
            </select>
          </label>
          <label>Region<input type="text" placeholder="e.g. Punjab" value={region} onChange={(e) => { setRegion(e.target.value); setPage(1); }} /></label>
          <label>From<input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} /></label>
          <label>To<input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} /></label>
        </div>
        <ErrorBox error={docs.error} />
        {docs.data && docs.data.items.length === 0 && <Empty>No reports match.</Empty>}
        {docs.data && docs.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Journey ID</th><th>Route</th><th>Region</th><th>Risk</th><th>Score</th><th>Decision</th><th>Created</th><th>Actions</th></tr></thead>
              <tbody>
                {docs.data.items.map((d) => (
                  <tr key={d.document_id}>
                    <td className="nowrap"><button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(d.document_id)}>{d.document_code}</button>
                      {d.demo_data && <div><span className="pill pill-muted">demo data</span></div>}</td>
                    <td>{d.route_name}</td>
                    <td className="small">{d.region}</td>
                    <td><RiskPill level={d.risk_level} /></td>
                    <td>{d.journey_score}</td>
                    <td className="small">{d.decision}</td>
                    <td className="nowrap small">{fmtDate(d.created_at)}</td>
                    <td className="nowrap">
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => openPdf(`/documents/${d.document_id}/preview`)}>View</button>{" "}
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => downloadFile(`/documents/${d.document_id}/download`, `${d.document_code}.pdf`)}>Download</button>{" "}
                      {confirmDelete === d.document_id ? (
                        <>
                          <button type="button" className="btn btn-danger btn-sm" onClick={() => remove(d.document_id)}>Confirm delete</button>{" "}
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(null)}>Keep</button>
                        </>
                      ) : (
                        <button type="button" className="btn btn-danger btn-sm" onClick={() => setConfirmDelete(d.document_id)}>Delete</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {docs.data && docs.data.total > 25 && (
          <div className="row" style={{ marginTop: 12 }}>
            <button type="button" className="btn btn-ghost btn-sm" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="small muted">Page {page} of {Math.ceil(docs.data.total / 25)} · {docs.data.total} reports</span>
            <button type="button" className="btn btn-ghost btn-sm" disabled={page * 25 >= docs.data.total} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </Card>
      {selected && <ReportDetail id={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function ReportDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const d = useQuery({ queryKey: ["document", id], queryFn: () => api.document(id) });
  const doc = d.data;
  return (
    <Card title={doc ? `${doc.document_code} — audit details` : "Loading…"} actions={<button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>}>
      <ErrorBox error={d.error} />
      {doc && (
        <div className="grid-2">
          <dl className="kv">
            <dt>Route</dt><dd>{doc.route_name}</dd>
            <dt>Decision</dt><dd>{doc.decision} <StatusPill status={doc.risk_level ?? ""} /></dd>
            <dt>Narrative</dt><dd>{doc.narrative_source} · {doc.model}</dd>
            <dt>Route data</dt><dd>{Object.entries(doc.providers).map(([k, v]) => `${k}: ${v}`).join(" · ")}</dd>
            <dt>PDF SHA-256</dt><dd className="small"><code>{doc.pdf_sha256.slice(0, 32)}…</code></dd>
            <dt>Pages</dt><dd>{doc.page_count}</dd>
          </dl>
          <dl className="kv">
            {Object.entries(doc.versions).map(([k, v]) => (<Fragment key={k}><dt>{k.replace("_", " ")} version</dt><dd>{v}</dd></Fragment>))}
            <dt>Claude calls</dt><dd>{doc.usage.calls}</dd>
            <dt>Tokens (in / cached / out)</dt><dd>{doc.usage.input_tokens} / {doc.usage.cache_read_input_tokens} / {doc.usage.output_tokens}</dd>
            <dt>Estimated cost</dt><dd>{fmtMoney(doc.usage.estimated_cost_usd, "USD")} · {fmtMoney(doc.usage.estimated_cost_inr, "INR")}</dd>
          </dl>
        </div>
      )}
    </Card>
  );
}
