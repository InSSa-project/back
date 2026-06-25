from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str = Field(default='', alias='OPENAI_API_KEY')
    gemini_api_key: str = Field(default='', alias='GEMINI_API_KEY')
    gms_api_key: str = Field(default='', alias='GMS_KEY')
    gemini_api_base_url: str = Field(
        default='https://gms.ssafy.io/gmsapi',
        alias='GEMINI_API_BASE_URL',
    )
    gms_base_url: str = Field(default='https://gms.ssafy.io/gmsapi', alias='GMS_BASE_URL')
    gms_model: str = Field(default='gpt-5.2', alias='GMS_MODEL')
    llm_provider: str = Field(default='openai', alias='LLM_PROVIDER')
    llm_request_timeout: float = Field(default=20, alias='LLM_REQUEST_TIMEOUT')
    llm_request_retries: int = Field(default=0, alias='LLM_REQUEST_RETRIES')
    llm_http_base_url: str = Field(default='https://gms.ssafy.io/gmsapi', alias='LLM_HTTP_BASE_URL')
    llm_http_endpoint: str = Field(default='/v1/chat/completions', alias='LLM_HTTP_ENDPOINT')
    llm_http_api_key: str = Field(default='', alias='LLM_HTTP_API_KEY')
    llm_http_model: str = Field(default='', alias='LLM_HTTP_MODEL')
    default_chat_model: str = Field(default='gpt-5.2', alias='DEFAULT_CHAT_MODEL')
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
    max_completion_tokens: int = Field(default=300, alias='MAX_COMPLETION_TOKENS')
    max_answer_chars: int = Field(default=700, alias='MAX_ANSWER_CHARS')
    max_schedule_answer_items: int = Field(default=8, alias='MAX_SCHEDULE_ANSWER_ITEMS')
    max_context_chunks: int = Field(default=4, alias='MAX_CONTEXT_CHUNKS')
    max_few_shots: int = Field(default=2, alias='MAX_FEW_SHOTS')
    lora_reasoner_enabled: bool = Field(default=False, alias='LORA_REASONER_ENABLED')
    lora_base_model: str = Field(default='Qwen/Qwen2.5-3B-Instruct', alias='LORA_BASE_MODEL')
    lora_adapter_path: str = Field(
        default='ai_server/finetuning/outputs/inssa_qwen2_5_3b_mvp',
        alias='LORA_ADAPTER_PATH',
    )
    lora_max_new_tokens: int = Field(default=180, alias='LORA_MAX_NEW_TOKENS')
    lora_temperature: float = Field(default=0.2, alias='LORA_TEMPERATURE')
    lora_top_p: float = Field(default=0.85, alias='LORA_TOP_P')
    lora_repetition_penalty: float = Field(default=1.2, alias='LORA_REPETITION_PENALTY')
    lora_preload_on_startup: bool = Field(default=True, alias='LORA_PRELOAD_ON_STARTUP')

    class Config:
        env_file = '.env'
        extra = 'ignore'


@lru_cache
def get_settings():
    return Settings()


