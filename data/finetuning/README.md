# INSSA LoRA/Fine-tuning Dataset Workspace

목적: SSAFY 분위기, 문화, 말투, 멘토링 대화 패턴, 평가 관련 상담 패턴을 LoRA 실험용 데이터셋으로 정리한다.

## 폴더 구조

- raw/culture: 싸피 분위기, 반 문화, 서비스 캐릭터 참고 자료
- raw/mentoring: 멘토링/상담 대화 원본 또는 요약
- raw/evaluations: 과목평가/월말평가 관련 원본 자료
- raw/notices: 공지/안내문 원본 참고 자료
- curated/intent_classification: 질문 의도 분류 학습 샘플
- curated/answer_style: 답변 말투/형식 학습 샘플
- curated/mentoring_dialogues: 멘토링 대화형 학습 샘플
- curated/evaluation_guidance: 평가/과락/수료 상담 샘플
- exports: 학습기에 넣을 jsonl 결과물
- schemas: 데이터셋 포맷 정의

## 원칙

1. 최신 공지/공식 기준 자체는 파인튜닝에 외우게 하지 않는다. RAG 근거로 사용한다.
2. 개인 성적, 이름, 반, Mattermost ID 등 개인정보는 제거한다.
3. LoRA에는 말투, 상담 방식, 의도 분류, 답변 형식 위주로 학습시킨다.
4. 공식 규정은 단정하지 않고 근거 기반으로 답변하도록 만든다.
5. raw와 exports는 git에 올리지 않는다.

## 추천 데이터 포맷

```json
{"instruction":"SSAFY 생활 지원 AI 비서답게 답변하라.","input":"과락 맞았는데 너무 힘들어","output":"많이 부담됐겠어요. 지금은 결과를 바로 단정하기보다 남은 평가 수와 복구 가능성을 먼저 확인하는 게 좋아요."}
```

## 다음 작업

1. raw 폴더에 원본 자료를 넣는다.
2. 개인정보/민감정보를 제거한다.
3. curated 폴더에 질문-답변 샘플로 재작성한다.
4. jsonl로 export한다.
5. LoRA 실험 모델에 투입한다.
