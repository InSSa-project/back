from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str = Field(default='', alias='OPENAI_API_KEY')
    gemini_api_key: str = Field(default='', alias='GEMINI_API_KEY')
    llm_provider: str = Field(default='openai', alias='LLM_PROVIDER')
    default_chat_model: str = Field(default='gpt-4.1-mini', alias='DEFAULT_CHAT_MODEL')
    default_gemini_model: str = Field(default='gemini-2.5-flash', alias='DEFAULT_GEMINI_MODEL')
    embedding_model: str = Field(default='text-embedding-3-small', alias='EMBEDDING_MODEL')
    embedding_provider: str = Field(default='auto', alias='EMBEDDING_PROVIDER')
    vectorstore_provider: str = Field(default='faiss', alias='VECTORSTORE_PROVIDER')
    vectorstore_path: str = Field(default='var/rag/faiss_index.json', alias='VECTORSTORE_PATH')
    top_k: int = Field(default=5, alias='RAG_TOP_K')
    rerank_top_k: int = Field(default=3, alias='RERANK_TOP_K')
    retrieval_score_threshold: float = Field(default=0.08, alias='RETRIEVAL_SCORE_THRESHOLD')
    chunk_size: int = Field(default=800, alias='CHUNK_SIZE')
    chunk_overlap: int = Field(default=120, alias='CHUNK_OVERLAP')
    max_prompt_tokens: int = Field(default=2400, alias='MAX_PROMPT_TOKENS')
    max_context_chunks: int = Field(default=4, alias='MAX_CONTEXT_CHUNKS')
    max_few_shots: int = Field(default=2, alias='MAX_FEW_SHOTS')

    class Config:
        env_file = '.env'
        extra = 'ignore'


@lru_cache
def get_settings():
    return Settings()
