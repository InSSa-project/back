from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.schemas.chat import ChatRequest, ChatResponse

router = APIRouter()


@router.post('', response_model=ChatResponse)
def create_chat_response(request: ChatRequest):
    return ChatPipeline().run(request)


@router.post('/stream')
def stream_chat_response(request: ChatRequest):
    return StreamingResponse(
        ChatPipeline().stream(request),
        media_type='text/event-stream',
    )
