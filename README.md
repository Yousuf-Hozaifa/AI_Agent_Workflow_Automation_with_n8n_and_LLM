---
title: AI Lead Handler – Mysoft Heaven BD Ltd
emoji: 🤖
colorFrom: indigo
colorTo: purple
sdk: docker
pinned: false
license: mit
short_description: AI-powered lead classifier and auto-responder using LLaMA 3.1
---

# 🤖 AI Lead Handler

**Mysoft Heaven (BD) Ltd. — Assessment 01: AI Agent Workflow Automation**

An AI-powered lead handling system that classifies intent, extracts key fields, and generates personalised replies automatically using **LLaMA 3.1** via Groq.

## Features
- **Intent Classification**: Sales / Support / Spam
- **Field Extraction**: Name, Company, Requirement, Email, Phone
- **AI-Generated Replies**: Personalised for each lead type
- **REST API**: Full JSON API with Swagger docs at `/docs`
- **Web Form**: Submit leads directly from the browser

## API Usage

```bash
curl -X POST https://YOUR-SPACE.hf.space/leads \
  -H "Content-Type: application/json" \
  -d '{"message": "Hi, I need a demo of your enterprise plan", "sender_email": "you@company.com"}'
```

## Setup

Set `GROQ_API_KEY` in your Space secrets (Settings → Variables and secrets).
