"""
LiveWire Audit API
==================
A FastAPI wrapper around livewire_auto_collect.py and livewire_audit_generator.py.

Exposes one main endpoint:
  POST /generate-audit
    Body: { "url": "...", "company": "...", "email": "...", "name": "..." }
    Returns: { "status": "ok", "pdf_path": "...", "score": 38, "findings": {...} }

Health check:
  GET /
  GET /health

Deploy target: Render.com (free tier)
"""

import os
import sys
import json
import subprocess
import tempfile
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, HttpUrl, EmailStr

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

# Where your existing Python scripts live (same directory as this file)
SCRIPT_DIR = Path(__file__).parent.resolve()
AUTO_COLLECT_SCRIPT = SCRIPT_DIR / "livewire_auto_collect.py"
PDF_GENERATOR_SCRIPT = SCRIPT_DIR / "livewire_audit_generator.py"

# Where generated files go (Render gives us /tmp on free tier)
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/livewire-reports"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Optional security: require an API key in the request header to prevent abuse
# Set this in Render's environment variables. Leave unset for testing.
API_KEY = os.getenv("LIVEWIRE_API_KEY", "")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("livewire-api")

# ----------------------------------------------------------------------------
# FastAPI app setup
# ----------------------------------------------------------------------------

app = FastAPI(
    title="LiveWire Audit API",
    description="Generates branded AI-readiness audit PDFs for prospect websites.",
    version="1.0.0",
)

# Allow CORS so the Make.com webhook and your site can hit this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------------
# Request / response models
# ----------------------------------------------------------------------------

class AuditRequest(BaseModel):
    url: str          # e.g. "https://davesplumbingmt.com"
    company: str      # e.g. "Dave's Plumbing"
    email: Optional[str] = None     # prospect email (for emailing the PDF later)
    name: Optional[str] = None      # prospect contact name
    api_key: Optional[str] = None   # optional security key

class AuditResponse(BaseModel):
    status: str
    company: str
    url: str
    score: Optional[int] = None
    pdf_filename: Optional[str] = None
    pdf_download_url: Optional[str] = None
    findings_summary: Optional[dict] = None
    error: Optional[str] = None

# ----------------------------------------------------------------------------
# Health check endpoints
# ----------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "LiveWire Audit API",
        "status": "running",
        "version": "1.0.0",
        "scripts_found": {
            "auto_collect": AUTO_COLLECT_SCRIPT.exists(),
            "pdf_generator": PDF_GENERATOR_SCRIPT.exists(),
        },
    }

@app.get("/health")
def health():
    """Lightweight check for Render's uptime monitoring."""
    if not AUTO_COLLECT_SCRIPT.exists() or not PDF_GENERATOR_SCRIPT.exists():
        raise HTTPException(503, "Required Python scripts not found.")
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# ----------------------------------------------------------------------------
# Main audit endpoint
# ----------------------------------------------------------------------------

@app.post("/generate-audit", response_model=AuditResponse)
def generate_audit(req: AuditRequest):
    """
    Run the full pipeline: scrape site → build findings JSON → render PDF.
    Returns the PDF download URL plus a summary.
    """
    # ---- Security check ----
    if API_KEY and req.api_key != API_KEY:
        raise HTTPException(401, "Invalid API key.")

    # ---- Validation ----
    if not AUTO_COLLECT_SCRIPT.exists():
        raise HTTPException(500, f"Missing script: {AUTO_COLLECT_SCRIPT.name}")
    if not PDF_GENERATOR_SCRIPT.exists():
        raise HTTPException(500, f"Missing script: {PDF_GENERATOR_SCRIPT.name}")

    if not req.url.startswith(("http://", "https://")):
        req.url = "https://" + req.url

    log.info(f"Audit request: {req.company} @ {req.url}")

    # ---- Use a temp working directory for this job ----
    job_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_company = "".join(c if c.isalnum() else "_" for c in req.company)[:50]
    job_dir = OUTPUT_DIR / f"{safe_company}_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)

    findings_json = job_dir / "findings.json"

    # ---- Step 1: Run the auto-collect script ----
    try:
        log.info(f"Running auto-collect: {req.url}")
        collect_result = subprocess.run(
            [
                sys.executable, str(AUTO_COLLECT_SCRIPT),
                req.url,
                "--company", req.company,
                "--output", str(findings_json),
            ],
            capture_output=True,
            text=True,
            timeout=120,  # 2 minute max
        )
        if collect_result.returncode != 0:
            log.error(f"Auto-collect failed: {collect_result.stderr}")
            return AuditResponse(
                status="error",
                company=req.company,
                url=req.url,
                error=f"Auto-collect failed: {collect_result.stderr[:500]}",
            )
    except subprocess.TimeoutExpired:
        return AuditResponse(
            status="error",
            company=req.company,
            url=req.url,
            error="Auto-collect timed out (site took too long to respond).",
        )

    # ---- Step 2: Verify the findings JSON was produced ----
    if not findings_json.exists():
        return AuditResponse(
            status="error",
            company=req.company,
            url=req.url,
            error="Auto-collect did not produce findings.json",
        )

    try:
        with open(findings_json) as f:
            findings_data = json.load(f)
    except Exception as e:
        return AuditResponse(
            status="error",
            company=req.company,
            url=req.url,
            error=f"Could not parse findings JSON: {e}",
        )

    # ---- Step 3: Run the PDF generator ----
    try:
        log.info("Generating PDF...")
        pdf_result = subprocess.run(
            [
                sys.executable, str(PDF_GENERATOR_SCRIPT),
                str(findings_json),
                "--output", str(job_dir),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pdf_result.returncode != 0:
            log.error(f"PDF generation failed: {pdf_result.stderr}")
            return AuditResponse(
                status="error",
                company=req.company,
                url=req.url,
                error=f"PDF generation failed: {pdf_result.stderr[:500]}",
            )
    except subprocess.TimeoutExpired:
        return AuditResponse(
            status="error",
            company=req.company,
            url=req.url,
            error="PDF generation timed out.",
        )

    # ---- Step 4: Find the generated PDF ----
    pdfs = list(job_dir.glob("*.pdf"))
    if not pdfs:
        return AuditResponse(
            status="error",
            company=req.company,
            url=req.url,
            error="PDF generator ran but no PDF file was created.",
        )

    pdf_file = pdfs[0]
    log.info(f"PDF created: {pdf_file.name}")

    # ---- Step 5: Build a summary for the response ----
    summary = {
        "scores": findings_data.get("scores", {}),
        "headline_finding": findings_data.get("headline_finding", ""),
        "schema_coverage": findings_data.get("schema_coverage", {}),
    }
    overall_score = findings_data.get("scores", {}).get("overall")

    return AuditResponse(
        status="ok",
        company=req.company,
        url=req.url,
        score=overall_score,
        pdf_filename=pdf_file.name,
        pdf_download_url=f"/download-pdf/{job_dir.name}/{pdf_file.name}",
        findings_summary=summary,
    )

# ----------------------------------------------------------------------------
# PDF download endpoint
# ----------------------------------------------------------------------------

@app.get("/download-pdf/{job_id}/{filename}")
def download_pdf(job_id: str, filename: str):
    """Serve the generated PDF back to whoever needs it (Make.com, you, etc)."""
    pdf_path = OUTPUT_DIR / job_id / filename
    if not pdf_path.exists():
        raise HTTPException(404, "PDF not found.")
    if not pdf_path.suffix.lower() == ".pdf":
        raise HTTPException(400, "Not a PDF file.")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=filename,
    )

# ----------------------------------------------------------------------------
# Local dev entry point
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("livewire_api:app", host="0.0.0.0", port=port, reload=True)
