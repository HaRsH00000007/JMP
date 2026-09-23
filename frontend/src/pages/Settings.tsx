import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, getToken, setToken } from "../api/client";
import { Card, ErrorBox, RiskPill, fmtMoney } from "../components/ui";

export default function Settings() {
  const s = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const cost = useQuery({ queryKey: ["cost-model"], queryFn: api.costModel });
  const hz = useQuery({ queryKey: ["hazards"], queryFn: api.hazards, staleTime: 300_000 });
  const [token, setTok] = useState(getToken());
  const [saved, setSaved] = useState(false);
  const qc = useQueryClient();

  return (
    <>
      <div className="page-head"><div><h1>Settings</h1><p className="lead">Effective configuration (read-only — set through environment variables and the versioned rule files).</p></div></div>
      <ErrorBox error={s.error} />
      <div className="grid-2">
        <Card title="API access">
          <label className="field">Bearer token
            <input type="password" value={token} onChange={(e) => { setTok(e.target.value); setSaved(false); }} placeholder={s.data?.auth_required ? "required by this server" : "not required by this server"} />
            <span className="hint">Stored only in this browser. Needed when the server sets API_AUTH_TOKEN.</span>
          </label>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => { setToken(token.trim()); setSaved(true); qc.invalidateQueries(); }}>Save</button>
          {saved && <span className="small muted" style={{ marginLeft: 8 }}>Saved.</span>}
        </Card>
        {s.data && (
          <Card title="Versions">
            <dl className="kv">
              <dt>Application</dt><dd>{s.data.app_version}</dd>
              <dt>JMP template</dt><dd>{s.data.template_version}</dd>
              <dt>Hazard library</dt><dd>{s.data.hazard_library_version}</dd>
              <dt>Prompt / schema</dt><dd>{s.data.prompt_version} / {s.data.schema_version}</dd>
              <dt>Scoring / rules</dt><dd>{s.data.scoring_version} / {s.data.rules_version}</dd>
            </dl>
          </Card>
        )}
      </div>
      {s.data && (
        <Card title="Generation">
          <dl className="kv">
            <dt>Claude model</dt><dd>{s.data.llm.model} ({s.data.llm.provider}{s.data.llm.api_key_configured ? ", key configured" : ", no key"})</dd>
            <dt>Effort · cache TTL</dt><dd>{s.data.llm.effort} · {s.data.llm.cache_ttl}</dd>
            <dt>Validation attempts</dt><dd>{s.data.llm.max_attempts}</dd>
            <dt>Refusal fallbacks</dt><dd>{s.data.llm.fallbacks_enabled ? "enabled (server-side, default)" : "disabled"}</dd>
            <dt>Bulk narrative mode</dt><dd>{s.data.llm.bulk_mode} · concurrency {s.data.llm.concurrency}</dd>
            <dt>Providers</dt><dd>{Object.entries(s.data.providers).map(([k, v]) => `${k}: ${v}`).join(" · ")}</dd>
            <dt>Risk band displayed</dt><dd>{s.data.report.display_band_method}</dd>
            <dt>Hazard text</dt><dd>{s.data.report.hazard_display_text}{s.data.report.short_controls_approved ? " · EHS short controls approved" : " · short controls pending EHS approval (verbatim bullets used)"}</dd>
            <dt>Hazard pointers</dt><dd>{s.data.report.hazard_pointer_count} per report</dd>
            <dt>Bulk limits</dt><dd>{s.data.bulk.max_rows} rows · {Math.round(s.data.bulk.max_bytes / 1048576)} MB</dd>
            <dt>USD → INR</dt><dd>{s.data.usd_to_inr}</dd>
          </dl>
        </Card>
      )}
      {cost.data && (
        <Card title="Estimated Claude cost (token profile: ~7.5k cached prefix, ~2.5k variable input, ~4.5k output)">
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Model</th><th>Tier</th><th>Per JMP</th><th>Per 100</th><th>Per 1,000</th><th>Per 1,000 (INR)</th></tr></thead>
              <tbody>
                {cost.data.projections.map((p, i) => (
                  <tr key={i} style={p.model === cost.data!.configured_model ? { fontWeight: 600 } : undefined}>
                    <td>{p.model}{p.model === cost.data!.configured_model ? " (configured)" : ""}</td><td>{p.tier}</td>
                    <td>{fmtMoney(p.usd.per_jmp, "USD")}</td><td>{fmtMoney(p.usd.per_100, "USD")}</td>
                    <td>{fmtMoney(p.usd.per_1000, "USD")}</td><td>{fmtMoney(p.inr.per_1000, "INR")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">Estimates; actual per-JMP cost from recorded usage is on the Dashboard. See docs/cost-model.md.</p>
        </Card>
      )}
      {hz.data && (
        <Card title={`Hazard library v${hz.data.version} — 25 hazards (verbatim from the Excel)`}>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Code</th><th>Hazard</th><th>Sev.</th><th>Prob.</th><th>RPN</th><th>Band</th><th>Matrix</th><th>Detection</th></tr></thead>
              <tbody>
                {hz.data.hazards.map((h) => (
                  <tr key={h.code}>
                    <td>{h.code}</td><td>{h.name}</td><td>{h.severity}</td><td>{h.probability}</td><td>{h.rpn}</td>
                    <td><RiskPill level={h.severity_band} /></td><td className="small">{h.matrix_zone}</td>
                    <td className="small">{h.detection.kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
