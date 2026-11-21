import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import "./App.css";

const API = `http://${window.location.hostname}:8000`;

function Icon({ name, size = 18 }) {
  const paths = {
    shield: <><path d="M12 3 5 6v5c0 4.4 2.8 7.6 7 9 4.2-1.4 7-4.6 7-9V6l-7-3Z" /><path d="m9 12 2 2 4-4" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    upload: <><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" /><path d="M5 14v4h14v-4" /></>,
    play: <path d="m9 6 9 6-9 6V6Z" />,
    trash: <><path d="M4 7h16M10 11v5m4-5v5" /><path d="m6 7 1 13h10l1-13M9 7V4h6v3" /></>,
    file: <><path d="M6 3h8l4 4v14H6V3Z" /><path d="M14 3v5h5M9 13h6m-6 4h6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    alert: <><path d="M12 4 3 20h18L12 4Z" /><path d="M12 10v4m0 3h.01" /></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  };
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function traffic(score) {
  if (score >= 70) return { label: "High", tone: "danger" };
  if (score >= 40) return { label: "Medium", tone: "warning" };
  return { label: "Low", tone: "success" };
}

function statusPill(file) {
  if (file.status !== "Done") return file.status;
  if (file.similarity_score >= 70 || file.ai_score >= 70) return "Flagged";
  if (file.similarity_score >= 40 || file.ai_score >= 40) return "Review";
  return "Safe";
}

function statusTone(status) {
  return { Done: "success", Safe: "success", Review: "warning", Flagged: "danger", Error: "danger" }[status] || "neutral";
}

export default function App() {
  const [sessionName, setSessionName] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [session, setSession] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [analysisRunning, setAnalysisRunning] = useState(false);
  const [error, setError] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef(null);

  const fileStats = useMemo(() => {
    const files = session?.files || [];
    const total = files.length;
    const done = files.filter((f) => f.status === "Done").length;
    const errors = files.filter((f) => f.status === "Error").length;
    const running = total > 0 && done + errors < total;
    const pct = total === 0 ? 0 : Math.round(((done + errors) / total) * 100);
    const flagged = files.filter((f) => statusPill(f) === "Flagged").length;
    return { total, done, errors, running, pct, flagged };
  }, [session]);

  async function refreshSession(id) { const res = await axios.get(`${API}/sessions/${id}`); setSession(res.data); if (res.data.files.length && res.data.files.every((file) => ["Done", "Error"].includes(file.status))) setAnalysisRunning(false); return res.data; }
  function resetToCreate() { setSessionId(null); setSession(null); setSelectedFiles([]); setLoading(false); setAnalysisRunning(false); setError(""); setSessionName(""); }

  async function startSession() {
    setLoading(true); setError("");
    try { const res = await axios.post(`${API}/sessions`, { name: sessionName }); setSessionId(res.data.session_id); await refreshSession(res.data.session_id); }
    catch (e) { setError(e?.response?.data?.detail || (e?.request ? `Backend unavailable at ${API}. Start the FastAPI server first.` : e.message) || "Failed to create session"); }
    finally { setLoading(false); }
  }

  async function upload() {
    if (!sessionId || selectedFiles.length === 0) return;
    setLoading(true); setError("");
    try { const form = new FormData(); for (const f of selectedFiles) form.append("files", f); await axios.post(`${API}/sessions/${sessionId}/upload`, form); setSelectedFiles([]); if (fileInputRef.current) fileInputRef.current.value = ""; await refreshSession(sessionId); }
    catch (e) { setError(e?.response?.data?.detail || "Upload failed"); }
    finally { setLoading(false); }
  }

  function addDroppedFiles(fileList) {
    const incoming = Array.from(fileList || []);
    const pdfs = incoming.filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) {
      setError("Please drop PDF files only.");
      return;
    }
    if (pdfs.length !== incoming.length) setError("Only PDF files were added; other file types were skipped.");
    setSelectedFiles((current) => {
      const existing = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      return [...current, ...pdfs.filter((file) => !existing.has(`${file.name}:${file.size}:${file.lastModified}`))];
    });
  }

  async function analyze() {
    if (!sessionId) return;
    setLoading(true); setAnalysisRunning(true); setError("");
    try { await axios.post(`${API}/sessions/${sessionId}/analyze`); await refreshSession(sessionId); }
    catch (e) { setAnalysisRunning(false); setError(e?.response?.data?.detail || (e?.request ? `Backend unavailable at ${API}.` : e.message) || "Analysis failed"); }
    finally { setLoading(false); }
  }

  async function deleteSession() {
    if (!sessionId || !window.confirm("Delete this session? This will remove it from the database and delete uploaded files.")) return;
    setLoading(true); setError("");
    try { await axios.delete(`${API}/sessions/${sessionId}`); resetToCreate(); }
    catch (e) { setError(e?.response?.data?.detail || "Delete failed"); setLoading(false); }
  }

  useEffect(() => { if (!sessionId) return undefined; const t = setInterval(() => refreshSession(sessionId).catch(() => {}), 2000); return () => clearInterval(t); }, [sessionId]);
  const hasSession = Boolean(sessionId);

  return <div className="app-shell">
    <main>{error && <div className="alert" role="alert"><Icon name="alert" size={18} /><span>{error}</span></div>}
      {!hasSession && <section className="welcome-layout page-enter"><div className="hero-panel"><div className="hero-kicker"><span className="kicker-line" /> DOCUMENT INTELLIGENCE</div><h2>Make every submission<br /><em>count.</em></h2><p>Compare PDFs for shared content and AI-like writing signals in one focused workspace.</p><div className="hero-points"><span><Icon name="check" size={15} /> Batch PDF comparison</span><span><Icon name="check" size={15} /> Explainable risk signals</span><span><Icon name="check" size={15} /> Human-first review</span></div></div><div className="card create-card"><div className="eyebrow">GET STARTED</div><h2>Start a new session</h2><p className="muted">Name your batch to keep files and results organized.</p><label className="field-label" htmlFor="session-name">Session name</label><input id="session-name" className="input" value={sessionName} onChange={(e) => setSessionName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && startSession()} placeholder="e.g. ENG101 · Essay 1" /><button className="button button-primary button-wide" onClick={startSession} disabled={!sessionName.trim() || loading}>{loading ? "Creating session…" : <>Create session <Icon name="arrow" size={17} /></>}</button><div className="disclaimer"><Icon name="shield" size={15} /> Results are triage signals. Always verify manually.</div></div></section>}
      {hasSession && session && <section className="dashboard page-enter">
        <div className="session-title-row"><div className="page-title">{session.name}</div><button className="button button-ghost" onClick={resetToCreate} disabled={loading}><Icon name="plus" size={16} /> New session</button></div>
        <div className="workspace-grid"><div className="card upload-card"><div className="section-heading"><div><div className="eyebrow">STEP 01</div><h3>Add documents</h3></div><span className="step-icon"><Icon name="upload" size={19} /></span></div><p className="muted">Upload one or more PDF files to compare them as a batch.</p><button className={`dropzone${isDragging ? " is-dragging" : ""}`} onClick={() => fileInputRef.current?.click()} onDragEnter={(e) => { e.preventDefault(); setIsDragging(true); }} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }} onDragLeave={() => setIsDragging(false)} onDrop={(e) => { e.preventDefault(); setIsDragging(false); addDroppedFiles(e.dataTransfer.files); }} type="button"><span className="upload-icon"><Icon name="upload" size={22} /></span><strong>{isDragging ? "Drop PDF files here" : selectedFiles.length ? `${selectedFiles.length} file${selectedFiles.length > 1 ? "s" : ""} selected` : "Choose or drop PDF files"}</strong><span>Drag multiple PDFs here, or click to browse</span></button><input ref={fileInputRef} className="visually-hidden" type="file" multiple accept="application/pdf" onChange={(e) => setSelectedFiles(Array.from(e.target.files || []))} /><div className="button-row"><button className="button button-primary" onClick={upload} disabled={loading || selectedFiles.length === 0}><Icon name="upload" size={16} />{loading ? "Uploading…" : `Upload ${selectedFiles.length ? `(${selectedFiles.length})` : "files"}`}</button><button className="button button-secondary" onClick={analyze} disabled={loading || analysisRunning || session.files.length === 0}><Icon name="play" size={16} />{analysisRunning ? "Analyzing…" : "Run analysis"}</button></div><div className="tip"><Icon name="shield" size={15} /><span><b>How scoring works</b><br />Similarity uses fuzzy chunk matching. AI score combines weak writing signals.</span></div></div><div className="card results-card"><div className="section-heading"><div><div className="eyebrow">STEP 02</div><h3>Review results</h3></div><span className="result-count">{fileStats.total} {fileStats.total === 1 ? "document" : "documents"}</span></div>{session.files.length === 0 ? <div className="empty-state"><span className="empty-icon"><Icon name="file" size={23} /></span><strong>Your results will appear here</strong><span>Upload PDFs to begin checking for similarity and AI-like signals.</span></div> : <div className="table-wrap"><table className="table"><thead><tr><th>Document</th><th>Similarity</th><th>AI signal</th><th>Status</th></tr></thead><tbody>{session.files.map((f) => { const sim = traffic(f.similarity_score); const ai = traffic(f.ai_score); const status = statusPill(f); return <tr key={f.id}><td><div className="file-cell"><span className="file-icon"><Icon name="file" size={16} /></span><div><div className="file-name">{f.filename}</div><div className="small">{f.error || `Document ID ${f.id}`}</div></div></div></td><td><span className={`score ${sim.tone}`}><b>{Math.round(f.similarity_score)}%</b><small>{sim.label}</small></span></td><td><span className={`score ${ai.tone}`}><b>{Math.round(f.ai_score)}%</b><small>{ai.label}</small></span></td><td><span className={`status-pill ${statusTone(status)}`}><span />{status}</span></td></tr>; })}</tbody></table></div>}</div></div></section>}
    </main>
  </div>;
}
