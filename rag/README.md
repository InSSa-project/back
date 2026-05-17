# INSSA RAG Layer

`rag/` is an independent retrieval layer. It does not own PostgreSQL models.
Django apps create and store `raw_ssafy_data` and `ai_documents`; this package
chunks `ai_documents`, creates embeddings, stores vectors, retrieves top-k
chunks, and builds prompts for AI services.
