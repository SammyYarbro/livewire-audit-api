# Phase 1: Deploy LiveWire Audit API to Render

This wraps your existing Python scripts in a web service so they can be called
from Make.com, your website, or anywhere else. Total setup time: ~30 minutes.

---

## What You're Deploying

```
Caller (Make.com or you)
    ↓
    POST https://your-api.onrender.com/generate-audit
    Body: {"url": "...", "company": "..."}
    ↓
Render runs livewire_auto_collect.py → findings.json
    ↓
Render runs livewire_audit_generator.py → PDF
    ↓
Returns: {"status": "ok", "pdf_download_url": "/download-pdf/..."}
```

---

## Step 1 — Put All Files in One GitHub Repo

You should have a folder on your computer that looks like this when you're done:

```
livewire-audit-api/
├── livewire_api.py                  ← NEW (this is the API wrapper)
├── livewire_auto_collect.py         ← YOUR EXISTING SCRIPT
├── livewire_audit_generator.py      ← YOUR EXISTING SCRIPT
├── requirements.txt                 ← NEW (Python dependencies)
├── render.yaml                      ← NEW (Render config)
└── README.md                        ← NEW (this file)
```

### To put it on GitHub:

1. Go to github.com → click **+ → New repository**
2. Name it `livewire-audit-api`
3. Make it **Public** (Render free tier requires public repos)
4. Click **Create repository**
5. On the empty repo page, click **uploading an existing file**
6. Drag all 6 files into the upload area
7. Click **Commit changes**

---

## Step 2 — Deploy to Render

1. Go to https://render.com → sign in with GitHub
2. Click **New + → Web Service**
3. Click **Connect** next to your `livewire-audit-api` repo
4. Render will auto-detect the `render.yaml` file and prefill everything
5. Confirm:
   - **Name:** livewire-audit-api
   - **Region:** Oregon (closest to most US users)
   - **Branch:** main
   - **Build Command:** `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command:** `uvicorn livewire_api:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
6. Click **Create Web Service**
7. Wait 3-5 minutes for the first build

When it's done, Render gives you a URL like:
```
https://livewire-audit-api.onrender.com
```

That's your endpoint. Save it.

---

## Step 3 — Test It

### Test 1: Health check (browser)

Open in your browser:
```
https://YOUR-RENDER-URL.onrender.com/
```

You should see:
```json
{
  "service": "LiveWire Audit API",
  "status": "running",
  "version": "1.0.0",
  "scripts_found": {
    "auto_collect": true,
    "pdf_generator": true
  }
}
```

If `scripts_found` shows `false` for either script — your scripts didn't upload to GitHub. Go back and fix that.

### Test 2: Run a real audit (curl or Postman)

Open Command Prompt (Windows) or Terminal (Mac) and paste this:

```bash
curl -X POST https://YOUR-RENDER-URL.onrender.com/generate-audit \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://example.com\",\"company\":\"Example Inc\"}"
```

Wait 60-90 seconds. You should get back:

```json
{
  "status": "ok",
  "company": "Example Inc",
  "url": "https://example.com",
  "score": 38,
  "pdf_filename": "LiveWire_Audit_Example_Inc_20260623.pdf",
  "pdf_download_url": "/download-pdf/Example_Inc_20260623_120000/LiveWire_Audit_Example_Inc_20260623.pdf",
  "findings_summary": {...}
}
```

### Test 3: Download the PDF

Take the `pdf_download_url` from above, prepend your Render URL:

```
https://YOUR-RENDER-URL.onrender.com/download-pdf/Example_Inc_.../LiveWire_Audit_...pdf
```

Paste in your browser. The PDF should download.

**If all three tests pass — Phase 1 is complete.**

---

## Things to Know About Render Free Tier

| Limitation | Impact |
|---|---|
| Service sleeps after 15 min of no traffic | First request takes ~30 sec to wake up. Subsequent requests are instant. |
| 750 hours/month free | Plenty for occasional use. |
| Files in `/tmp` get wiped on restart | Don't store anything important there. PDFs are fine for short-term download. |
| Public IP only | Anyone with the URL can hit it. Set `LIVEWIRE_API_KEY` to prevent abuse. |

---

## Adding API Key Security (Recommended Before Phase 3)

To prevent random people from running up your costs:

1. In Render dashboard → your service → **Environment** tab
2. Add a new env var:
   - Key: `LIVEWIRE_API_KEY`
   - Value: any random string you make up (e.g. `livewire-prod-2026-x7k9`)
3. Save → Render auto-redeploys
4. Now all requests must include `"api_key": "livewire-prod-2026-x7k9"` in the body

---

## Common Problems

**"Application failed to start" in Render logs**
→ Check the build logs. Usually a missing dependency in requirements.txt.

**Health check returns `scripts_found: false`**
→ The Python scripts weren't uploaded to GitHub. Push them.

**Audit times out**
→ Some sites block scrapers. Check `livewire_auto_collect.py` user-agent string.

**PDF generation fails**
→ Usually a missing field in findings.json. Run the scripts locally first to verify.

---

## What's Next

Once Phase 1 works, we move to:

**Phase 2:** Wire in Resend so PDFs auto-email instead of just being downloaded.
**Phase 3:** Connect Formspree → Make.com → this endpoint so everything is automatic.
**Phase 4:** Add monitoring + error notifications so you know if something breaks.

Don't move to Phase 2 until all three Phase 1 tests pass.
