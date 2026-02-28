"""
AI Lead Handler - FastAPI Backend
Assessment 01: AI Agent Workflow Automation
"""

import os
import json
import uuid
import logging
import asyncio
from datetime import datetime
from typing import Optional
from pathlib import Path

# ─── Load .env FIRST before anything else reads os.getenv ────────────────────
# This must happen before any os.getenv() calls
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, validator
from contextlib import asynccontextmanager

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Config (reads AFTER .env is loaded) ──────────────────────────────────────
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
SMTP_HOST       = os.getenv("SMTP_HOST", "")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
SMTP_SENDER     = os.getenv("SMTP_SENDER", "leads@example.com")
SMTP_PASS       = os.getenv("SMTP_PASS", "")

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# ─── In-memory datastore ──────────────────────────────────────────────────────
LEADS_DB: dict[str, dict] = {}


# ─── Pydantic models ──────────────────────────────────────────────────────────
class LeadRequest(BaseModel):
    message: str
    source: Optional[str] = "api"
    sender_email: Optional[str] = None   # NOTE: field is "sender_email" not "email"

    @validator("message")
    def message_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("message must not be empty")
        return v.strip()


class LeadResponse(BaseModel):
    lead_id: str
    intent: str
    extracted: dict
    ai_reply: str
    status: str
    timestamp: str


# ─── App lifespan ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Print key status on startup so you can immediately see if it loaded
    if GROQ_API_KEY:
        masked = GROQ_API_KEY[:8] + "..." + GROQ_API_KEY[-4:]
        logger.info(f"✅  GROQ_API_KEY loaded: {masked}")
    else:
        logger.error("❌  GROQ_API_KEY is MISSING — check your .env file!")
        logger.error("    Expected file at: " + str(Path(__file__).parent / ".env"))
    logger.info(f"🤖  Model: {GROQ_MODEL}")
    logger.info("🚀  AI Lead Handler is ready")
    yield
    logger.info("👋  Shutting down")


app = FastAPI(
    title="AI Lead Handler",
    description="Classifies leads, extracts fields, stores data, replies automatically.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Prompts ──────────────────────────────────────────────────────────────────
CLASSIFY_PROMPT = """\
You are a lead classifier for a B2B SaaS company called Mysoft Heaven (BD) Ltd.
Analyse the incoming message and respond ONLY with valid JSON — no extra text, no markdown fences.

Required JSON schema:
{
  "intent": "Sales",
  "confidence": 0.95,
  "name": "full name from message or null",
  "company": "company name from message or null",
  "requirement": "one-sentence summary of what they need or null",
  "email": "email address from message or null",
  "phone": "phone number from message or null"
}

Intent rules:
- "Sales"   → buying interest, pricing, demo requests, partnership enquiries
- "Support" → existing customer issues, bugs, how-to questions, complaints
- "Spam"    → advertisements, gibberish, completely irrelevant content

Important:
- Never invent information not present in the message.
- If a field cannot be reliably extracted, use null (not empty string).
- Output ONLY the JSON object. No explanation. No markdown.
"""

REPLY_PROMPT = """\
You are a professional customer success agent at Mysoft Heaven (BD) Ltd.
Write a warm, helpful reply (3-5 sentences) based on the lead details below.

Intent     : {intent}
Name       : {name}
Company    : {company}
Requirement: {requirement}
Message    : {message}

Guidelines:
- Sales   → thank them, confirm interest, offer a discovery call within 24 hours
- Support → empathise, confirm you received the issue, give a realistic response timeline
- Spam    → one polite sentence declining

Do NOT invent specific pricing, dates, SLAs, or guarantees.
End with: "The Team at Mysoft Heaven (BD) Ltd."
"""


# ─── LLM call with retry ──────────────────────────────────────────────────────
async def call_llm(system: str, user: str, max_tokens: int = 512) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. "
            "Make sure your .env file exists in the backend/ folder and contains GROQ_API_KEY=..."
        )

    payload = {
        "model": GROQ_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.3,          # lower = more deterministic / less hallucination
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                logger.info(f"Groq responded OK (attempt {attempt})")
                return content

        except httpx.HTTPStatusError as e:
            last_error = e
            logger.warning(f"Groq HTTP {e.response.status_code} on attempt {attempt}")
            if e.response.status_code == 429:   # rate limit → wait longer
                await asyncio.sleep(RETRY_DELAY * attempt * 2)
            elif e.response.status_code >= 500: # server error → retry
                await asyncio.sleep(RETRY_DELAY * attempt)
            else:
                raise  # 4xx other than 429 → don't retry

        except Exception as e:
            last_error = e
            logger.warning(f"Groq call attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)

    raise RuntimeError(f"Groq API failed after {MAX_RETRIES} attempts. Last error: {last_error}")


# ─── Classify ─────────────────────────────────────────────────────────────────
async def classify_lead(message: str) -> dict:
    raw = await call_llm(CLASSIFY_PROMPT, message, max_tokens=300)
    logger.info(f"Raw classification response: {raw[:200]}")
    try:
        # Clean markdown fences (use replace, NOT lstrip)
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
        # Validate required keys exist
        for key in ("intent", "confidence", "name", "company", "requirement", "email", "phone"):
            if key not in data:
                data[key] = None
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode failed: {e}. Raw: {raw[:300]}")
        return {
            "intent": "Unknown", "confidence": 0.0,
            "name": None, "company": None,
            "requirement": None, "email": None, "phone": None,
        }


# ─── Generate reply ───────────────────────────────────────────────────────────
async def generate_reply(cls: dict, message: str) -> str:
    prompt = REPLY_PROMPT.format(
        intent=cls.get("intent", "Unknown"),
        name=cls.get("name") or "there",
        company=cls.get("company") or "your company",
        requirement=cls.get("requirement") or "your inquiry",
        message=message[:600],
    )
    return await call_llm("You are a professional customer success agent.", prompt, max_tokens=300)


# ─── Optional helpers ─────────────────────────────────────────────────────────
async def forward_to_n8n(data: dict):
    if not N8N_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(N8N_WEBHOOK_URL, json=data)
        logger.info("Forwarded to n8n ✅")
    except Exception as e:
        logger.warning(f"n8n forward failed (non-fatal): {e}")


async def send_email(to: str, subject: str, body: str):
    if not SMTP_HOST or not to:
        return
    import smtplib
    from email.mime.text import MIMEText
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = SMTP_SENDER
        msg["To"]      = to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.starttls()
            if SMTP_PASS:
                srv.login(SMTP_SENDER, SMTP_PASS)
            srv.sendmail(SMTP_SENDER, [to], msg.as_string())
        logger.info(f"Email sent to {to}")
    except Exception as e:
        logger.warning(f"Email failed (non-fatal): {e}")


# ─── Web form UI ──────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Lead Handler – Mysoft Heaven (BD) Ltd.</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',sans-serif;background:linear-gradient(135deg,#667eea22,#764ba222);
         min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
    .card{background:#fff;border-radius:16px;box-shadow:0 8px 40px rgba(0,0,0,.12);
          padding:40px;width:100%;max-width:580px}
    .logo{font-size:1.6rem;font-weight:800;color:#4c3bbd;margin-bottom:4px}
    .sub{color:#718096;font-size:.9rem;margin-bottom:28px}
    label{display:block;font-size:.82rem;font-weight:700;color:#4a5568;
          margin-bottom:5px;text-transform:uppercase;letter-spacing:.04em}
    input,textarea{width:100%;border:1.5px solid #e2e8f0;border-radius:8px;
                   padding:10px 14px;font-size:.95rem;font-family:inherit;
                   margin-bottom:16px;outline:none;transition:border .2s}
    input:focus,textarea:focus{border-color:#667eea;box-shadow:0 0 0 3px #667eea22}
    textarea{resize:vertical;min-height:110px}
    button{width:100%;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;
           border:none;border-radius:8px;padding:13px;font-size:1rem;font-weight:700;
           cursor:pointer;transition:opacity .2s;letter-spacing:.02em}
    button:hover{opacity:.88} button:disabled{opacity:.55;cursor:not-allowed}
    #result{margin-top:24px;display:none}
    .badge{display:inline-block;padding:4px 14px;border-radius:999px;
           font-size:.78rem;font-weight:800;margin-bottom:14px;letter-spacing:.05em}
    .Sales{background:#c6f6d5;color:#22543d}
    .Support{background:#bee3f8;color:#2a4a7f}
    .Spam{background:#fed7d7;color:#742a2a}
    .Unknown{background:#e2e8f0;color:#4a5568}
    .section{font-size:.78rem;font-weight:700;color:#667eea;
             text-transform:uppercase;letter-spacing:.06em;margin:12px 0 5px}
    pre{background:#f7fafc;border:1px solid #e2e8f0;border-radius:8px;
        padding:12px;font-size:.84rem;color:#2d3748;white-space:pre-wrap;word-break:break-word}
    .err{color:#e53e3e;font-size:.9rem;padding:12px;background:#fff5f5;
         border-radius:8px;border:1px solid #fed7d7}
  </style>
</head>
<body>
<div class="card">
  <div class="logo">🤖 AI Lead Handler</div>
  <p class="sub">Mysoft Heaven (BD) Ltd. — Intelligent Lead Processing</p>
  <label for="email">Your Email (optional)</label>
  <input id="email" type="email" placeholder="you@company.com">
  <label for="msg">Your Message *</label>
  <textarea id="msg" placeholder="Hi, I'm interested in your enterprise plan and would like to schedule a demo…"></textarea>
  <button id="btn" onclick="go()">Submit Lead →</button>
  <div id="result"></div>
</div>
<script>
async function go(){
  const msg=document.getElementById('msg').value.trim();
  const email=document.getElementById('email').value.trim();
  const btn=document.getElementById('btn');
  const out=document.getElementById('result');
  if(!msg){alert('Please enter a message.');return;}
  btn.textContent='⏳ Processing…';btn.disabled=true;out.style.display='none';
  try{
    const r=await fetch('/leads',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:msg,sender_email:email||null,source:'form'})});
    const d=await r.json();
    out.style.display='block';
    if(!r.ok){out.innerHTML='<p class="err">Error: '+(d.detail||JSON.stringify(d))+'</p>';return;}
    out.innerHTML=`
      <span class="badge ${d.intent}">${d.intent}</span>
      <p class="section">Lead ID</p><pre>${d.lead_id}</pre>
      <p class="section">Extracted Fields</p><pre>${JSON.stringify(d.extracted,null,2)}</pre>
      <p class="section">AI-Generated Reply</p><pre>${d.ai_reply}</pre>`;
  }catch(e){out.style.display='block';out.innerHTML='<p class="err">Network error: '+e.message+'</p>';}
  finally{btn.textContent='Submit Lead →';btn.disabled=false;}
}
document.getElementById('msg').addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.key==='Enter')go();
});
</script>
</body>
</html>"""


# ─── API routes ───────────────────────────────────────────────────────────────
@app.post("/leads", response_model=LeadResponse)
async def create_lead(payload: LeadRequest, background_tasks: BackgroundTasks):
    lead_id   = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    logger.info(f"[{lead_id}] New lead — source={payload.source}")

    # 1. Classify & extract
    try:
        cls = await classify_lead(payload.message)
    except Exception as e:
        logger.error(f"[{lead_id}] Classification error: {e}")
        cls = {
            "intent": "Unknown", "confidence": 0.0,
            "name": None, "company": None,
            "requirement": None, "email": None, "phone": None,
        }

    intent = cls.get("intent", "Unknown")
    logger.info(f"[{lead_id}] intent={intent}  confidence={cls.get('confidence')}")

    # 2. Generate reply
    try:
        ai_reply = await generate_reply(cls, payload.message)
    except Exception as e:
        logger.error(f"[{lead_id}] Reply error: {e}")
        ai_reply = "Thank you for reaching out! A member of our team will follow up shortly. — The Team at Mysoft Heaven (BD) Ltd."

    # 3. Store
    record = {
        "lead_id":      lead_id,
        "timestamp":    timestamp,
        "source":       payload.source,
        "raw_message":  payload.message,
        "sender_email": payload.sender_email,
        "intent":       intent,
        "confidence":   cls.get("confidence"),
        "extracted": {
            "name":        cls.get("name"),
            "company":     cls.get("company"),
            "requirement": cls.get("requirement"),
            "email":       cls.get("email") or payload.sender_email,
            "phone":       cls.get("phone"),
        },
        "ai_reply": ai_reply,
        "status":   "processed",
    }
    LEADS_DB[lead_id] = record
    logger.info(f"[{lead_id}] Stored. Total leads in DB: {len(LEADS_DB)}")

    # 4. Background tasks (non-blocking)
    background_tasks.add_task(forward_to_n8n, record)
    if payload.sender_email:
        background_tasks.add_task(
            send_email, payload.sender_email,
            "We received your message – Mysoft Heaven (BD) Ltd.", ai_reply
        )

    return LeadResponse(
        lead_id=lead_id, intent=intent,
        extracted=record["extracted"],
        ai_reply=ai_reply, status="processed", timestamp=timestamp,
    )


@app.get("/leads")
async def list_leads(limit: int = 50):
    """View all stored leads."""
    records = list(LEADS_DB.values())[-limit:]
    return {"total": len(LEADS_DB), "leads": records}


@app.get("/leads/{lead_id}")
async def get_lead(lead_id: str):
    if lead_id not in LEADS_DB:
        raise HTTPException(404, "Lead not found")
    return LEADS_DB[lead_id]


@app.get("/health")
async def health():
    """Health check — also shows if GROQ key is loaded."""
    return {
        "status":        "ok",
        "groq_key_set":  bool(GROQ_API_KEY),
        "groq_key_hint": (GROQ_API_KEY[:8] + "...") if GROQ_API_KEY else "NOT SET",
        "model":         GROQ_MODEL,
        "leads_stored":  len(LEADS_DB),
    }


# Webhook alias for n8n
@app.post("/webhook/lead")
async def webhook_lead(payload: LeadRequest, background_tasks: BackgroundTasks):
    payload.source = payload.source or "webhook"
    return await create_lead(payload, background_tasks)