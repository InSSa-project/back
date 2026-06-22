import gradio as gr
import subprocess
import os
from pathlib import Path
import json
from datetime import datetime


class TrainingUI:
    def __init__(self):
        self.process = None
        self.output_dir = Path("back/ai_server/finetuning/output_adapter")
        self.log_file = Path("back/ai_server/finetuning/training_log.txt")

    def check_data_file(self, train_file: str):
        """데이터 파일 존재 확인"""
        if not os.path.exists(train_file):
            return f"❌ 데이터 파일을 찾을 수 없습니다: {train_file}"
        
        # 파일 크기 확인
        size = os.path.getsize(train_file)
        size_mb = size / (1024 * 1024)
        
        # 줄 수 확인
        with open(train_file, 'r', encoding='utf-8') as f:
            lines = sum(1 for _ in f)
        
        return f"✓ 데이터 파일 확인됨\n- 경로: {train_file}\n- 크기: {size_mb:.2f} MB\n- 샘플 수: {lines}"

    def start_training(self, base_model: str, train_file: str, epochs: int, batch_size: int, 
                      learning_rate: float, lora_r: int, lora_alpha: int):
        """학습 시작"""
        
        # 데이터 파일 확인
        if not os.path.exists(train_file):
            return f"❌ 데이터 파일을 찾을 수 없습니다: {train_file}"
        
        # 명령어 구성
        cmd = [
            "python",
            "back/ai_server/finetuning/train_lora.py",
            "--base-model", base_model,
            "--train-file", train_file,
            "--output-dir", str(self.output_dir),
            "--epochs", str(epochs),
            "--train-batch-size", str(batch_size),
            "--learning-rate", str(learning_rate),
            "--lora-r", str(lora_r),
            "--lora-alpha", str(lora_alpha),
            "--logging-steps", "10",
            "--save-steps", "50",
        ]
        
        # 로그 파일 초기화
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write(f"[{datetime.now().isoformat()}] 학습 시작\n")
            f.write(f"Command: {' '.join(cmd)}\n")
            f.write("=" * 80 + "\n\n")
        
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                bufsize=1
            )
            
            output = []
            for line in self.process.stdout:
                output.append(line.strip())
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(line)
            
            self.process.wait()
            
            if self.process.returncode == 0:
                return "✓ 학습 완료!\n\n" + "\n".join(output[-30:])
            else:
                return f"❌ 학습 중 오류 발생 (코드: {self.process.returncode})\n\n" + "\n".join(output[-30:])
        
        except Exception as e:
            return f"❌ 오류: {str(e)}"

    def get_training_status(self):
        """학습 상태 확인"""
        if not self.output_dir.exists():
            return "아직 학습이 시작되지 않았습니다."
        
        status = []
        status.append("=" * 60)
        status.append("📊 학습 상태")
        status.append("=" * 60)
        
        # Checkpoint 확인
        checkpoints = sorted([d for d in self.output_dir.iterdir() if d.is_dir() and d.name.startswith("checkpoint-")])
        if checkpoints:
            latest_ckpt = checkpoints[-1]
            status.append(f"\n✓ 최신 체크포인트: {latest_ckpt.name}")
            status.append(f"  경로: {latest_ckpt}")
        
        # Adapter 파일 확인
        adapter_file = self.output_dir / "adapter_model.bin"
        if adapter_file.exists():
            size = adapter_file.stat().st_size / (1024 * 1024)
            status.append(f"\n✓ LoRA 어댑터 파일 존재")
            status.append(f"  크기: {size:.2f} MB")
            status.append(f"  경로: {adapter_file}")
        
        # 시각화 파일 확인
        viz_files = ["loss.png", "learning_rate.png", "metrics.png"]
        viz_found = []
        for viz in viz_files:
            viz_path = self.output_dir / viz
            if viz_path.exists():
                viz_found.append(viz)
        
        if viz_found:
            status.append(f"\n✓ 생성된 그래프: {', '.join(viz_found)}")
        
        # 토크나이저 확인
        tokenizer_files = ["tokenizer.model", "tokenizer.json", "tokenizer_config.json"]
        for tok_file in tokenizer_files:
            tok_path = self.output_dir / tok_file
            if tok_path.exists():
                status.append(f"\n✓ 토크나이저: {tok_file}")
                break
        
        # 전체 저장 경로
        status.append(f"\n📁 전체 저장 경로: {self.output_dir.absolute()}")
        status.append("\n" + "=" * 60)
        
        return "\n".join(status)

    def get_log(self):
        """학습 로그 반환"""
        if not self.log_file.exists():
            return "아직 로그가 없습니다."
        
        with open(self.log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return content[-3000:] if len(content) > 3000 else content  # 마지막 3000자

    def stop_training(self):
        """학습 중단"""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            return "✓ 학습이 중단되었습니다."
        return "실행 중인 학습이 없습니다."


def create_interface():
    """Gradio 인터페이스 생성"""
    ui = TrainingUI()
    
    with gr.Blocks(title="LoRA Fine-tuning UI", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🚀 LoRA Fine-tuning UI")
        gr.Markdown("간단한 클릭으로 모델 학습을 시작하세요!")
        
        with gr.Tabs():
            # 탭 1: 학습 설정
            with gr.Tab("1️⃣ 학습 설정"):
                gr.Markdown("### 데이터 및 모델 설정")
                
                with gr.Row():
                    train_file = gr.Textbox(
                        value="data/train.jsonl",
                        label="📂 훈련 데이터 파일 경로",
                        placeholder="예: data/train.jsonl"
                    )
                    check_btn = gr.Button("✓ 파일 확인", variant="primary")
                
                file_status = gr.Textbox(label="파일 상태", interactive=False, lines=3)
                
                check_btn.click(ui.check_data_file, inputs=[train_file], outputs=[file_status])
                
                gr.Markdown("---")
                gr.Markdown("### 모델 설정")
                
                with gr.Row():
                    base_model = gr.Textbox(
                        value="meta-llama/Llama-2-7b-chat-hf",
                        label="🤖 Base Model",
                        placeholder="Hugging Face 모델 이름"
                    )
                
                gr.Markdown("---")
                gr.Markdown("### 학습 파라미터")
                
                with gr.Row():
                    epochs = gr.Slider(1, 10, value=2, step=1, label="에폭 수 (Epochs)")
                    batch_size = gr.Slider(1, 32, value=2, step=1, label="배치 크기 (Batch Size)")
                
                with gr.Row():
                    learning_rate = gr.Number(value=2e-4, label="학습률 (Learning Rate)")
                    lora_r = gr.Slider(4, 64, value=16, step=4, label="LoRA Rank (r)")
                
                with gr.Row():
                    lora_alpha = gr.Slider(8, 128, value=32, step=8, label="LoRA Alpha")
                
                gr.Markdown("---")
                
                with gr.Row():
                    start_btn = gr.Button("🚀 학습 시작", scale=2, variant="primary")
                    stop_btn = gr.Button("⏹️ 학습 중단", scale=1, variant="stop")
                
                training_output = gr.Textbox(label="학습 결과", interactive=False, lines=10)
                
                start_btn.click(
                    ui.start_training,
                    inputs=[base_model, train_file, epochs, batch_size, learning_rate, lora_r, lora_alpha],
                    outputs=[training_output]
                )
                stop_btn.click(ui.stop_training, outputs=[training_output])
            
            # 탭 2: 학습 상태
            with gr.Tab("2️⃣ 학습 상태"):
                gr.Markdown("### 현재 학습 상태 및 저장 위치")
                
                status_btn = gr.Button("🔄 상태 갱신", variant="primary")
                status_output = gr.Textbox(label="상태", interactive=False, lines=15)
                
                status_btn.click(ui.get_training_status, outputs=[status_output])
                
                # 처음 로딩 시 상태 표시
                demo.load(ui.get_training_status, outputs=[status_output])
            
            # 탭 3: 로그
            with gr.Tab("3️⃣ 학습 로그"):
                gr.Markdown("### 실시간 학습 로그")
                
                log_btn = gr.Button("🔄 로그 갱신", variant="primary")
                log_output = gr.Textbox(label="로그", interactive=False, lines=20)
                
                log_btn.click(ui.get_log, outputs=[log_output])
                
                # 처음 로딩 시 로그 표시
                demo.load(ui.get_log, outputs=[log_output])
            
            # 탭 4: 가중치 위치
            with gr.Tab("4️⃣ 저장 위치"):
                gr.Markdown("### 🏁 학습 완료 후 가중치 저장 위치")
                
                location_text = f"""
## 📁 가중치 저장 경로

모든 가중치와 파일은 다음 폴더에 저장됩니다:

```
{Path('back/ai_server/finetuning/output_adapter').absolute()}
```

### 파일 구조

```
output_adapter/
├── adapter_config.json         # LoRA 설정
├── adapter_model.bin           # LoRA 가중치 (핵심 파일)
├── config.json                 # 모델 설정
├── tokenizer.model             # 토크나이저
├── pytorch_model.bin           # 전체 모델 (선택사항)
├── training_log.jsonl          # 학습 로그
├── loss.png                    # 손실 곡선
├── learning_rate.png           # 학습률 변화
├── metrics.png                 # 전체 지표
├── sample_evaluations.jsonl    # 샘플 평가
└── checkpoint-*/               # 중간 저장 (학습 중단 시 재개 가능)
```

### 중간 저장 (Checkpoint)

학습 중에 50스텝마다 자동으로 `checkpoint-*` 폴더에 저장됩니다:

```
checkpoint-50/
checkpoint-100/
checkpoint-150/
...
```

이 체크포인트에서 학습을 재개할 수 있습니다.

### 핵심 파일

- **adapter_model.bin** (필수)
  - LoRA 어댑터의 가중치
  - 크기: 약 100-500MB (모델 크기에 따라 다름)

- **adapter_config.json** (필수)
  - LoRA 설정 파일
  - adapter_model.bin과 함께 필요

- **tokenizer.model** (필수)
  - 텍스트 토큰화 파일

### 사용 방법

학습 완료 후, 다음 명령어로 모델과 대화할 수 있습니다:

```bash
python back/ai_server/finetuning/interact.py \\
  --base-model meta-llama/Llama-2-7b-chat-hf \\
  --adapter-path back/ai_server/finetuning/output_adapter
```
                """
                
                gr.Markdown(location_text)
    
    return demo


if __name__ == "__main__":
    demo = create_interface()
    demo.launch(share=False, server_name="127.0.0.1", server_port=7860)
