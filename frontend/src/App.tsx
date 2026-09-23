import { useQuery } from "@tanstack/react-query";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import BulkJmp from "./pages/BulkJmp";
import Dashboard from "./pages/Dashboard";
import IndividualJmp from "./pages/IndividualJmp";
import Jobs from "./pages/Jobs";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/individual", label: "Individual JMP" },
  { to: "/bulk", label: "Bulk JMP" },
  { to: "/jobs", label: "Jobs" },
  { to: "/reports", label: "Generated Reports" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 60_000 });
  const s = settings.data;
  const demo = s && (s.llm.provider === "mock" || Object.values(s.providers).includes("mock"));
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">JMP</div>
          <div>
            <div className="brand-title">Journey Management Plan</div>
            <div className="brand-sub">Generator</div>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "nav active" : "nav")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          {s ? (
            <>
              <div>App {s.app_version} · Template v{s.template_version}</div>
              <div>Hazard library v{s.hazard_library_version} · Prompt v{s.prompt_version}</div>
              <div>Model {s.llm.provider === "mock" ? "mock (demo)" : s.llm.model}</div>
            </>
          ) : (
            <div>{settings.isError ? "API unreachable" : "Connecting…"}</div>
          )}
        </div>
      </aside>
      <main className="main">
        {demo && (
          <div className="alert alert-warn banner">
            <strong>Demo mode.</strong> {s!.llm.provider === "mock" ? "Narrative uses the template writer (no Claude key). " : ""}
            {Object.values(s!.providers).includes("mock") ? "Route data comes from mock providers. " : ""}
            Generated PDFs are watermarked “not for operational use”.
          </div>
        )}
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/individual" element={<IndividualJmp />} />
          <Route path="/bulk" element={<BulkJmp />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<div className="card">Page not found.</div>} />
        </Routes>
      </main>
    </div>
  );
}
