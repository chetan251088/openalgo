# OpenAlgo Market Pulse Project

## Purpose
OpenAlgo is a production-ready algorithmic trading platform built with Flask (backend) and React 19 (frontend). This worktree is building the Market Pulse feature — a comprehensive market analysis dashboard with LLM-powered analyst commentary.

## Tech Stack
- **Backend**: Python 3.12+, Flask, Flask-RESTX, SQLAlchemy
- **Frontend**: React 19, TypeScript, Vite, shadcn/ui, TanStack Query
- **Package Manager**: uv (required, never use global Python)
- **Database**: SQLite (multiple: openalgo.db, logs.db, latency.db, sandbox.db, historify.duckdb)
- **LLM Support**: Claude, OpenAI, Gemini, Ollama (provider-agnostic)

## Task: Market Pulse Analyst Layer (Task 8)
Creating `services/market_pulse_analyst.py` — an LLM analyst component that:
- Generates plain-English market commentary from scores/data
- Supports multiple LLM providers (Claude, OpenAI, Gemini, Ollama)
- Implements in-memory caching (5-min TTL by default)
- Falls back to rule-based analysis if LLM unavailable
- Non-blocking API (returns cached/fallback immediately, refreshes in background)

## Key Architecture
- 5 separate databases for isolation
- Flask blueprints for routes, restx_api for REST endpoints
- Broker integration pattern with plugin.json
- Unified WebSocket proxy (port 8765)
- Services layer for business logic
