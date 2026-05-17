import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai_server.api.v1.chat import router as chat_router
from ai_server.core.config import get_settings


app = FastAPI(title='INSSA AI Server', version='0.1.0')

app.include_router(chat_router, prefix='/v1/chat', tags=['chat'])


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            'error': exc.__class__.__name__,
            'message': str(exc),
            'traceback': traceback.format_exc(),
        },
    )


@app.get('/health')
def health_check():
    return {'status': 'ok'}


@app.get('/debug/config')
def debug_config():
    settings = get_settings()
    return {
        'openai_api_key_set': bool(settings.openai_api_key),
        'openai_api_key_prefix': settings.openai_api_key[:7] if settings.openai_api_key else '',
        'gemini_api_key_set': bool(settings.gemini_api_key),
        'gemini_api_key_prefix': settings.gemini_api_key[:7] if settings.gemini_api_key else '',
        'default_chat_model': settings.default_chat_model,
        'default_gemini_model': settings.default_gemini_model,
        'llm_provider': settings.llm_provider,
    }
