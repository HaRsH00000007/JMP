import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, downloadFile, openPdf, type JourneyRequest } from "../api/client";
import { Card, ErrorBox, StageTrack, StatusPill, fmtDate } from "../components/ui";

interface FormState {
  start: string;
  stops: string[];
  end: string;
  vehicle: "4W" | "2W";
  travelDate: string;
  departTime: string;
  manager: string;
  emergencyContact: string;
  hospital: string;
  police: string;
}

const EMPTY: FormState = {
  start: "", stops: [""], end: "", vehicle: "4W", travelDate: "", departTime: "",
  manager: "", emergencyContact: "", hospital: "", police: "",
};

const EXAMPLE: FormState = {
  ...EMPTY,
  start: "Paras Downtown Zirakpur, Punjab",
  stops: ["Chandigarh City Center Zirakpur, Punjab", "SBP Housing Park Society Derabassi, Punjab"],
  end: "Danone Nutricia India Plant, Lalru, Punjab",
};

export default function IndividualJmp() {
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const [f, setF] = useState<FormState>(EMPTY);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((s) => ({ ...s, [k]: v }));

  const submit = useMutation({
    mutationFn: (body: JourneyRequest) => api.generate(body),
    onSuccess: (r) => setParams({ job: r.job_id }),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const body: JourneyRequest = {
      start_location: f.start.trim(),
      stops: f.stops.map((s) => s.trim()).filter(Boolean),
      end_location: f.end.trim(),
      vehicle_type: f.vehicle,
    };
    if (f.travelDate) body.travel_date = f.travelDate;
    if (f.departTime) body.depart_time = f.departTime;
    if (f.manager.trim()) body.manager_name = f.manager.trim();
    if (f.emergencyContact.trim()) body.emergency_contact = f.emergencyContact.trim();
    if (f.hospital.trim()) body.nearest_hospital = f.hospital.trim();
    if (f.police.trim()) body.nearest_police = f.police.trim();
    submit.mutate(body);
  };

  const moveStop = (i: number, d: -1 | 1) =>
    setF((s) => {
      const stops = [...s.stops];
      const j = i + d;
      if (j < 0 || j >= stops.length) return s;
      [stops[i], stops[j]] = [stops[j], stops[i]];
      return { ...s, stops };
    });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Individual JMP</h1>
          <p className="lead">Enter the journey in travel order. Route, hazards and scores are computed by the application; Claude writes only the narrative.</p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={() => setF(EXAMPLE)}>Load example</button>
      </div>

      {jobId && <JobPanel jobId={jobId} onNew={() => { setParams({}); submit.reset(); }} />}

      <form onSubmit={onSubmit}>
        <div className="grid-2">
          <Card title="Route">
            <ol className="stop-list">
              <li>
                <span className="stop-num se">S</span>
                <input className="grow" type="text" required minLength={3} maxLength={300} placeholder="Starting location"
                  value={f.start} onChange={(e) => set("start", e.target.value)} aria-label="Starting location" style={{ flex: 1 }} />
              </li>
              {f.stops.map((s, i) => (
                <li key={i}>
                  <span className="stop-num">{i + 1}</span>
                  <input type="text" maxLength={300} placeholder={`Stop ${i + 1}`} value={s} style={{ flex: 1 }}
                    aria-label={`Stop ${i + 1}`}
                    onChange={(e) => setF((st) => ({ ...st, stops: st.stops.map((x, j) => (j === i ? e.target.value : x)) }))} />
                  <button type="button" className="icon-btn" title="Move up" onClick={() => moveStop(i, -1)} disabled={i === 0}>↑</button>
                  <button type="button" className="icon-btn" title="Move down" onClick={() => moveStop(i, 1)} disabled={i === f.stops.length - 1}>↓</button>
                  <button type="button" className="icon-btn" title="Remove stop"
                    onClick={() => setF((st) => ({ ...st, stops: st.stops.filter((_, j) => j !== i) }))}>✕</button>
                </li>
              ))}
              <li>
                <span className="stop-num se">E</span>
                <input type="text" required minLength={3} maxLength={300} placeholder="End location" value={f.end}
                  onChange={(e) => set("end", e.target.value)} aria-label="End location" style={{ flex: 1 }} />
              </li>
            </ol>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => set("stops", [...f.stops, ""])} disabled={f.stops.length >= 13}>
              + Add stop
            </button>
            <p className="small muted">Up to 13 intermediate stops. Use the same text for start and end for a round trip.</p>
          </Card>

          <Card title="Journey details">
            <label className="field">Vehicle type
              <div className="radio-group" role="radiogroup">
                {(["4W", "2W"] as const).map((v) => (
                  <label key={v} className={f.vehicle === v ? "on" : ""}>
                    <input type="radio" name="vehicle" value={v} checked={f.vehicle === v} onChange={() => set("vehicle", v)} />
                    {v === "4W" ? "4-Wheeler" : "2-Wheeler"}
                  </label>
                ))}
              </div>
              <span className="hint">Selects the 2W or 4W control set from the hazard library.</span>
            </label>
            <div className="form-grid">
              <label className="field">Travel date<input type="date" value={f.travelDate} onChange={(e) => set("travelDate", e.target.value)} />
                <span className="hint">Drives seasonal hazards (monsoon, fog).</span></label>
              <label className="field">Departure time<input type="time" value={f.departTime} onChange={(e) => set("departTime", e.target.value)} />
                <span className="hint">Drives night-driving and fatigue rules.</span></label>
              <label className="field">Manager name<input type="text" maxLength={200} value={f.manager} onChange={(e) => set("manager", e.target.value)} /></label>
              <label className="field">Emergency contact<input type="text" maxLength={200} value={f.emergencyContact} onChange={(e) => set("emergencyContact", e.target.value)} /></label>
              <label className="field">Nearest hospital<input type="text" maxLength={300} value={f.hospital} onChange={(e) => set("hospital", e.target.value)} /></label>
              <label className="field">Nearest police station<input type="text" maxLength={300} value={f.police} onChange={(e) => set("police", e.target.value)} /></label>
            </div>
            <p className="small muted">Fields from the hazard workbook's Header sheet. Supplied values are printed as <b>PROVIDED</b>; anything found by map search is marked <b>REQUIRES VERIFICATION</b>.</p>
          </Card>
        </div>
        <ErrorBox error={submit.error} />
        <div className="row">
          <button type="submit" className="btn btn-primary" disabled={submit.isPending}>
            {submit.isPending ? "Submitting…" : "Generate JMP"}
          </button>
          <span className="small muted">Generation runs in the background — you can leave this page and find the result under Jobs.</span>
        </div>
      </form>
    </>
  );
}

function JobPanel({ jobId, onNew }: { jobId: string; onNew: () => void }) {
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId),
    refetchInterval: (q) => (q.state.data && ["completed", "failed", "cancelled"].includes(q.state.data.status) ? false : 2000),
  });
  const j = job.data;
  return (
    <Card
      title={<>Job {j?.journey_code ?? jobId.slice(0, 8)} {j && <StatusPill status={j.status} />}</>}
      actions={<button type="button" className="btn btn-ghost btn-sm" onClick={onNew}>New journey</button>}
    >
      {job.error ? <ErrorBox error={job.error} /> : null}
      {j && (
        <>
          <StageTrack stage={j.stage} status={j.status} />
          <div className="small muted">{j.route.join(" → ")}</div>
          <div className="small muted">Queued {fmtDate(j.queued_at)}{j.finished_at ? ` · finished ${fmtDate(j.finished_at)}` : ""}{j.attempt > 1 ? ` · attempt ${j.attempt}` : ""}</div>
          {j.status === "failed" && j.error && (
            <div className="alert alert-bad" style={{ marginTop: 12 }}>
              <strong>{j.error.code}</strong> — {j.error.message}
              <div style={{ marginTop: 8 }}>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => api.retryJob(j.job_id).then(() => job.refetch())}>Retry from failed stage</button>
              </div>
            </div>
          )}
          {j.status === "completed" && j.document_id && (
            <div className="row" style={{ marginTop: 12 }}>
              <button type="button" className="btn btn-amber" onClick={() => downloadFile(`/documents/${j.document_id}/download`, `${j.journey_code}.pdf`)}>Download PDF</button>
              <button type="button" className="btn btn-ghost" onClick={() => openPdf(`/documents/${j.document_id}/preview`)}>View</button>
              <Link className="btn btn-ghost" to="/reports">All reports</Link>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
