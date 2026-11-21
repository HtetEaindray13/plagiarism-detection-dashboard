from fastapi import BackgroundTasks, FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime
import os
import uuid
import shutil
from pathlib import Path
from threading import Lock

from processing import extract_pdf_text, load_cached_text, similarity_between_texts, ai_likelihood_fuzzy


# -------------------
# App + CORS
# -------------------
app = FastAPI()
analysis_jobs: set[str] = set()
analysis_jobs_lock = Lock()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------
# DB (SQLite)
# -------------------
BASE_DIR = Path(__file__).resolve().parent
DB_URL = f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

UPLOAD_DIR = str(BASE_DIR / "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# -------------------
# Models
# -------------------
class Session(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    files = relationship("FileRecord", back_populates="session", cascade="all, delete-orphan")

class FileRecord(Base):
    __tablename__ = "files"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id"))
    filename = Column(String, nullable=False)
    status = Column(String, default="Queued")  # Queued / Processing / Done / Error
    similarity_score = Column(Float, default=0.0)
    ai_score = Column(Float, default=0.0)
    text_len = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="files")

Base.metadata.create_all(engine)

# -------------------
# Helpers
# -------------------
def db():
    return SessionLocal()

# -------------------
# Routes
# -------------------
@app.post("/sessions")
def create_session(payload: dict):
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Session name required")

    session_id = str(uuid.uuid4())
    s = db()
    s.add(Session(id=session_id, name=name))
    s.commit()
    return {"session_id": session_id, "name": name}

@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    s = db()
    sess = s.query(Session).filter(Session.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": sess.id,
        "name": sess.name,
        "created_at": sess.created_at.isoformat(),
        "files": [
            {
                "id": f.id,
                "filename": f.filename,
                "status": f.status,
                "similarity_score": f.similarity_score,
                "ai_score": f.ai_score,
                "error": f.error,
            }
            for f in sess.files
        ],
    }


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """Delete a session and its associated uploads/cache."""
    s = db()
    sess = s.query(Session).filter(Session.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    # Collect file ids before deleting DB rows
    file_ids = [f.id for f in sess.files]

    # Delete DB session (cascade -> FileRecord)
    s.delete(sess)
    s.commit()

    # Best-effort delete uploaded artifacts
    for fid in file_ids:
        for ext in (".pdf", ".txt"):
            p = os.path.join(UPLOAD_DIR, f"{fid}{ext}")
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                # Do not fail request if cleanup fails
                pass

    return {"ok": True, "message": "Session deleted"}

@app.post("/sessions/{session_id}/upload")
def upload_files(session_id: str, files: list[UploadFile] = File(...)):
    s = db()
    sess = s.query(Session).filter(Session.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    saved = []
    for uf in files:
        if not uf.filename.lower().endswith(".pdf"):
            continue

        file_id = str(uuid.uuid4())
        save_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")

        with open(save_path, "wb") as out:
            shutil.copyfileobj(uf.file, out)

        rec = FileRecord(
            id=file_id,
            session_id=session_id,
            filename=uf.filename,
            status="Queued",
        )
        s.add(rec)
        saved.append({"id": file_id, "filename": uf.filename})

    s.commit()
    return {"uploaded": saved}

def run_analysis(session_id: str):
    s = db()
    sess = s.query(Session).filter(Session.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    files = (
        s.query(FileRecord)
        .filter(FileRecord.session_id == session_id)
        .order_by(FileRecord.created_at.asc())
        .all()
    )
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # 1) Extract (cache) text
    for f in files:
        try:
            f.status = "Extracting"
            s.commit()

            text = extract_pdf_text(f.id)
            f.text_len = len(text)
            f.error = None
            s.commit()
        except Exception as e:
            f.status = "Error"
            f.error = str(e)
            s.commit()

    # 2) Similarity compare
    # Load each extracted document once. Re-reading the same text from disk for
    # every comparison makes larger batches unnecessarily slow.
    texts = {f.id: load_cached_text(f.id) for f in files}
    baseline = files[0]
    baseline_text = texts.get(baseline.id, "")

    for idx, f in enumerate(files):
        if f.status == "Error":
            continue

        f.status = "Comparing"
        s.commit()

        text_f = texts.get(f.id, "") or ""
        max_sim = 0.0

        # compare to baseline
        if f.id != baseline.id and baseline_text:
            max_sim = max(max_sim, similarity_between_texts(text_f, baseline_text))

        # compare to earlier files
        for j in range(0, idx):
            other = files[j]
            if other.status == "Error" or other.id == f.id:
                continue
            other_text = texts.get(other.id, "")
            if other_text:
                max_sim = max(max_sim, similarity_between_texts(text_f, other_text))

        f.similarity_score = float(max_sim)

        # 3) AI detection (heuristic for now)
        f.status = "AI Detecting"
        s.commit()

        try:
            f.ai_score = float(ai_likelihood_fuzzy(text_f))
        except Exception as e:
            f.ai_score = 0.0
            f.error = f"AI detect failed: {str(e)}"

        f.status = "Done"
        s.commit()

    s.close()
    return {"ok": True, "message": "Analysis complete."}


def run_analysis_job(session_id: str):
    """Run one analysis per session and always release its job lock."""
    try:
        run_analysis(session_id)
    finally:
        with analysis_jobs_lock:
            analysis_jobs.discard(session_id)


@app.post("/sessions/{session_id}/analyze", status_code=202)
def analyze_session(session_id: str, background_tasks: BackgroundTasks):
    """Start analysis without blocking the browser request."""
    s = db()
    try:
        sess = s.query(Session).filter(Session.id == session_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found")
        if not sess.files:
            raise HTTPException(status_code=400, detail="No files uploaded")
    finally:
        s.close()

    with analysis_jobs_lock:
        if session_id in analysis_jobs:
            return {"ok": True, "message": "Analysis is already in progress."}
        analysis_jobs.add(session_id)

    background_tasks.add_task(run_analysis_job, session_id)
    return {"ok": True, "message": "Analysis started."}
