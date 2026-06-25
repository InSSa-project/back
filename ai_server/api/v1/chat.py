from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ai_server.pipelines.chat_pipeline import ChatPipeline
from ai_server.schemas.chat import ChatRequest, ChatResponse

router = APIRouter()
_pipeline: ChatPipeline | None = None


def get_pipeline() -> ChatPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ChatPipeline()
    return _pipeline


@router.post('', response_model=ChatResponse)
def create_chat_response(request: ChatRequest):
    return get_pipeline().run(request)


@router.post('/stream')
def stream_chat_response(request: ChatRequest):
    return StreamingResponse(
        get_pipeline().stream(request),
        media_type='text/event-stream',
    )
