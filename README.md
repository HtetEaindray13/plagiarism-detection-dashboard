# IntegrityScan Dashboard

IntegrityScan is a local academic-integrity workspace for comparing PDF submissions. It lets you create a review session, upload multiple PDFs, run similarity analysis, and inspect AI-like writing signals.

> Results are heuristic triage signals. They should always be reviewed by a human before making an academic decision.

## Features

- Create and delete analysis sessions
- Upload multiple PDF files at once
- Drag and drop multiple PDFs into the upload area
- Extract and clean text from PDFs
- Compare documents using fuzzy chunk matching, shingle overlap, and n-gram similarity
- Calculate a heuristic AI-likelihood score
- View similarity, AI signal, and review status for each document
- Background analysis with live status updates
- Responsive React dashboard UI

## Project structure

```text
plagiarism-detection-dashboard/
├── backend/
│   ├── main.py
│   ├── processing.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   └── package.json
├── start-backend.cmd
└── README.md
```

## Requirements

- Python 3.13+ recommended
- Node.js 18+
- npm

## Run the backend

From the project root in Command Prompt:

```cmd
cd /d D:\plag-ai-dashboard\backend
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

You can also start the backend with:

```cmd
D:\plag-ai-dashboard\start-backend.cmd
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Interactive API documentation is available at `/docs`.

## Run the frontend

Open a second Command Prompt window:

```cmd
cd /d D:\plag-ai-dashboard\frontend
npm install
npm run dev
```

Open the URL shown by Vite, usually:

```text
http://localhost:5173
```

## Production build

```cmd
cd /d D:\plag-ai-dashboard\frontend
npm run build
```

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/sessions` | Create a session |
| `GET` | `/sessions/{session_id}` | Get session and file statuses |
| `DELETE` | `/sessions/{session_id}` | Delete a session and uploaded artifacts |
| `POST` | `/sessions/{session_id}/upload` | Upload one or more PDFs |
| `POST` | `/sessions/{session_id}/analyze` | Start background analysis |

## Data and privacy

The application stores its SQLite database at `backend/app.db` and uploaded files under `backend/uploads/`. These local runtime files are ignored by Git and are not included in the repository.

Do not use the heuristic scores as the sole basis for disciplinary or academic decisions.

## License

Add the project license here if this repository will be shared or distributed publicly.
