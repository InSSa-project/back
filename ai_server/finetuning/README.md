# LoRA Fine-tuning in `back/ai_server/finetuning`

간단한 3단계: 데이터 준비 → 클릭으로 학습 → 질문하기

## 🚀 빠른 시작: 웹 UI 사용

### 1. 필수 패키지 설치

```bash
pip install -r back/ai_server/finetuning/requirements.txt
```

### 2. 데이터 준비

JSONL 형식으로 데이터를 준비합니다. `진진막/data/train.jsonl`에 저장:

```json
{"instruction": "한국어로 자기소개해줘", "input": "", "output": "안녕하세요, 저는..."}
{"instruction": "이 텍스트를 요약해줘", "input": "긴 텍스트 내용...", "output": "요약 결과..."}
```

### 3. 웹 UI 실행

```bash
python back/ai_server/finetuning/ui.py
```

브라우저에서 `http://127.0.0.1:7860` 에 접속하면:

- **탭 1**: 데이터 파일 확인 → 모델 설정 → 🚀 **"학습 시작" 클릭**
- **탭 2**: 학습 상태 및 가중치 저장 위치 확인
- **탭 3**: 실시간 학습 로그
- **탭 4**: 가중치 저장 위치 상세 정보

### 4. 학습 완료 후

모델과 대화:

```bash
python back/ai_server/finetuning/interact.py \
  --base-model meta-llama/Llama-2-7b-chat-hf \
  --adapter-path back/ai_server/finetuning/output_adapter
```

---

## 📊 가중치 저장 위치

### 저장 경로
```
back/ai_server/finetuning/output_adapter/
├── adapter_model.bin           # ✓ LoRA 가중치 (핵심)
├── adapter_config.json         # ✓ LoRA 설정
├── tokenizer.model             # ✓ 토크나이저
├── config.json                 # 모델 설정
├── loss.png                    # 손실 곡선 그래프
├── learning_rate.png           # 학습률 변화 그래프
├── metrics.png                 # 전체 지표 그래프
├── training_log.jsonl          # 학습 로그
└── checkpoint-*/               # 중간 저장 (자동 저장)
```

### 중간 저장 (Checkpoint)

학습 중에 **50스텝마다** 자동으로 저장됩니다:

```bash
checkpoint-50/
checkpoint-100/
checkpoint-150/
```

학습이 중단되면 여기서 재개할 수 있습니다.

---

## 📝 수동 학습 (CLI 사용)

### 학습 시작

```bash
python back/ai_server/finetuning/train_lora.py \
  --base-model meta-llama/Llama-2-7b-chat-hf \
  --train-file data/train.jsonl \
  --output-dir back/ai_server/finetuning/output_adapter \
  --epochs 2 \
  --train-batch-size 2 \
  --learning-rate 2e-4 \
  --lora-r 16 \
  --lora-alpha 32
```

### 평가 데이터 포함

```bash
python back/ai_server/finetuning/train_lora.py \
  --base-model meta-llama/Llama-2-7b-chat-hf \
  --train-file data/train.jsonl \
  --eval-file data/eval.jsonl \
  --output-dir back/ai_server/finetuning/output_adapter \
  --epochs 3
```

---

## 🔧 학습 파라미터 설명

| 파라미터 | 설명 | 기본값 |
|---------|------|--------|
| `--base-model` | Hugging Face 모델 이름 | - |
| `--train-file` | 훈련 데이터 경로 | - |
| `--eval-file` | 평가 데이터 경로 (선택) | - |
| `--output-dir` | 가중치 저장 폴더 | - |
| `--epochs` | 반복 학습 횟수 | 2 |
| `--train-batch-size` | 배치 크기 | 2 |
| `--learning-rate` | 학습률 | 2e-4 |
| `--lora-r` | LoRA rank | 16 |
| `--lora-alpha` | LoRA alpha | 32 |
| `--lora-dropout` | LoRA dropout | 0.05 |

---

## ⚠️ 주의 사항

- **GPU 메모리 요구사항**
  - 7B 모델: 24GB 이상
  - 13B 모델: 40GB 이상
  - CPU 학습: 매우 느림 (권장하지 않음)

- **배치 크기 조정**
  - GPU 메모리 부족 시 `--train-batch-size 1`로 줄여보세요
  - 너무 작으면 학습 효율이 낮아집니다

- **데이터 품질**
  - 가중치 품질은 데이터 품질에 의존합니다
  - 한국어 도메인 데이터를 충분히 준비하세요 (최소 100+ 샘플)

---

## 🎯 전체 워크플로우

```
1. 데이터 준비 (data/train.jsonl)
   ↓
2. UI 실행 (python back/ai_server/finetuning/ui.py)
   ↓
3. 웹 브라우저에서 학습 설정 후 "🚀 학습 시작" 클릭
   ↓
4. 학습 진행 중 상태 확인 (탭 2, 3)
   ↓
5. 학습 완료 후 가중치 자동 저장
   ↓
6. 모델과 대화 (python back/ai_server/finetuning/interact.py ...)
```


