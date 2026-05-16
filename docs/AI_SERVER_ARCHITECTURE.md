# INSSA FastAPI AI Server Architecture

This document defines the implementation boundary for the FastAPI AI server.

The Django backend owns PostgreSQL models and REST API persistence. The AI
server owns query classification, retrieval, prompt building, LLM orchestration,
pseudo fine-tuning prompts, style analysis, OCR/VLM adapters, and streaming.

Core flow:

```text
Django chat API
→ FastAPI /v1/chat
→ Query classification
→ Vector retrieval
→ Reranking
→ Prompt builder
→ LLM provider
→ Answer + references
→ Django persists chat_messages and ai_chat_references
```
