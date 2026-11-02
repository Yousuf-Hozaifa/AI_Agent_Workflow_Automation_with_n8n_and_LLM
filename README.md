---
title: AI Lead Handler
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: AI lead classifier and auto-responder (FastAPI + Groq + n8n)
---

# 🤖 AI Lead Handler

AI agent workflow: **receive lead → classify intent → extract fields → generate reply → store → notify (n8n / email)**.

**Live demo:** `<add your Hugging Face Space URL>` · **API docs:** `/docs`

## Architecture
`Web form / REST / n8n webhook → FastAPI → Groq (Llama 3.1) → SQLite → n8n webhook (routing) + optional SMTP reply`

## Features
- Intent classification (Sales / Support / Spam) with JSON-mode output and validation
- Field extraction: name, company, requirement, email, phone (null when absent)
- Personalised AI reply; fallback reply and `needs_review` status if the LLM fails
- Retries with backoff, per-IP rate limit, prompt-injection-resistant prompts, XSS-safe UI
- Lead listing protected by `ADMIN_TOKEN`

## API
```bash
curl -X POST $URL/leads -H "Content-Type: application/json" \
  -d '{"message": "Hi, I need a demo of your enterprise plan", "sender_email": "you@company.com"}'
curl $URL/leads -H "X-Admin-Token: $ADMIN_TOKEN"
```

## Run locally
```bash
cp .env.example .env        # add GROQ_API_KEY and ADMIN_TOKEN
docker compose up --build   # API: :8000  n8n: :5678 (import n8n/lead_workflow.json)
```

## Deploy (Hugging Face Space)
Create a Docker Space, push this repo, then add `GROQ_API_KEY` and `ADMIN_TOKEN` under Settings → Variables and secrets.

## Limitations
Free Spaces have ephemeral storage and sleep when idle, so stored leads reset. Use an external DB for production.
