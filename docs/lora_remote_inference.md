# LoRA Remote Inference

배포 서버가 직접 LoRA 모델을 로드하지 않고, 로컬 GPU PC의 FastAPI 엔드포인트로 추론만 위임하는 구조다.

## Local GPU PC

```powershell
cd C:\Users\SSAFY\Desktop\진진막\back
$env:LORA_REMOTE_URL=""
$env:LORA_REASONER_ENABLED="true"
$env:LORA_PRELOAD_ON_STARTUP="false"
$env:LORA_REMOTE_API_KEY="change-me"
.\venv\Scripts\python.exe -m uvicorn ai_server.main:app --host 0.0.0.0 --port 8011
```

로컬 수신 엔드포인트:

```text
POST http://localhost:8011/v1/lora/generate
```

외부 배포 서버에서 접근해야 하면 Cloudflare Tunnel 같은 터널을 연결한다.

```powershell
cloudflared tunnel --url http://localhost:8011
```

## Deploy Server

배포 서버에는 모델을 올리지 않고 아래 값만 설정한다.

```env
LORA_REASONER_ENABLED=true
LORA_PRELOAD_ON_STARTUP=false
LORA_REMOTE_URL=https://your-tunnel-url.trycloudflare.com/v1/lora/generate
LORA_REMOTE_API_KEY=change-me
LORA_REMOTE_TIMEOUT=120
```

`LORA_REMOTE_URL`이 비어 있으면 기존처럼 같은 서버에서 로컬 LoRA 모델을 직접 로드한다.

## Safety

- 로컬 GPU 서버와 배포 서버의 `LORA_REMOTE_API_KEY`는 같은 값이어야 한다.
- 로컬 GPU 서버에는 `LORA_REMOTE_URL`을 비워둔다. 그래야 자기 자신을 다시 호출하지 않는다.
- 배포 서버는 `LORA_PRELOAD_ON_STARTUP=false`로 둔다. 시작 시 모델 로딩으로 서버가 멈추는 것을 막는다.
