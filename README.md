# Spiritual Oracle

A small FastAPI backend for a Bhagavad Gita question-answering oracle.

## Setup

1. Copy `.env.example` to `.env`.
2. Install dependencies with `uv sync`.
3. Run the app with `uv run app.main:app --reload`.

## Endpoints

- `GET /` - health check
- `POST /ask` - ask a question, requires `Authorization: Bearer <token>`
