# INSSA Gold Answer Benchmark

- Created: 2026-06-24T14:57:07
- Accuracy: 100.0%
- Success rate: 100.0%
- Fail rate: 0.0%
- Avg score: 80.1/100
- Success/Fail: 2/0
- Total: 2
- Avg latency: 34119.0 ms

## Category Summary

| Category | Total | Avg Score | Success Rate | Pass |
|---|---:|---:|---:|---:|
| project | 2 | 80.1 | 100.0% | 2 |

## Question Results

### Q1 - PASS / 81.7점

- Category: `project`
- Policy: `LORA_DIRECT`
- Issues: `none`
- Rubric: `intent=14.2, priority=20.0, context=14.0, action=18.5, safety=15.0`
- Rubric Reasons: `intent_understanding: question_hits=2, gold_hits=1; priority_judgment: priority_hits=3; ssafy_context: domain_hits=2; actionability: action_hits=5, list_bonus=0.0; safety: clean`
- Token F1: `0.0`
- Keyword Recall: `0.0`

**Question**

과락이 두 개 있는데 프로젝트 발표도 일주일 남았습니다. 무엇부터 하시겠습니까?

**Model Answer**

```text
프로젝트 준비는 시간 관리를 중요하게 생각해야 합니다. 먼저 현재 진행 상황에 대해 정리해보세요. 지금까지 완료했거나 아직 미완의 업무나 과제는 어떻게 되었고, 다음 주에는 어떤 일을 계획하고 있을까요? 기준적으로 한 번 점검하는 게 좋습니다. 답변을 종합할 때는 이렇게 작은 단계마다 나누어서 이해합니다. 처음 질문에서는 "두 가지 과락"이나 "일주일 남음" 정도로 봤지만, 실제 경험으로 보면 더 복잡하거나 길어질 수도 있습니다. 그래서 전체적인 맥락보다는 오늘 할 수 있는 부분부터 시작하려고 노력하세요.
```

**Gold Answer**

```text
시험공부를 먼저 하는 것을 추천합니다. 프로젝트는 필수 기능만 유지하면서 시험 대비에 집중하는 것이 좋습니다. 판단 이유: 프로젝트는 이후에도 진행할 기회가 있지만 시험 일정은 정해져 있어 일반적으로 시험의 우선순위가 더 높습니다.
```

---

### Q2 - PASS / 78.5점

- Category: `project`
- Policy: `LORA_DIRECT`
- Issues: `none`
- Rubric: `intent=13.0, priority=23.0, context=14.0, action=13.5, safety=15.0`
- Rubric Reasons: `intent_understanding: question_hits=1, gold_hits=2; priority_judgment: priority_hits=4; ssafy_context: domain_hits=2; actionability: action_hits=3, list_bonus=0.0; safety: clean`
- Token F1: `0.058`
- Keyword Recall: `0.1`

**Question**

프로젝트 기능은 많이 남았는데 시험도 다가옵니다. 어떻게 시간을 배분하시겠습니까?

**Model Answer**

```text
질문 을 보면 가장 큰 과제는 "현재 남아있는 프로젝트"입니다. 선생님 입장에서는 이렇게 말씀드리면 됩니다:

시험이 잡히기 전까지는 현재 진행 중인 일과 다음 주에 필요한 일을 우선 순위별로 나누세요. 지금 와서 모든 것을 끝내려고 하지 마세요. 중요한 것은 앞으로의 계획이니까요. 질문 있으신 건 언제든지 물어보세요. 처음부터 너무 걱정할 필요 없어요. 오늘 할 일이 있다면 그것으로 충분합니다.
```

**Gold Answer**

```text
최종관통 프로젝트 기간이라면 프로젝트에 더 집중하는 것이 좋습니다. 시험 범위는 유지 수준으로만 복습하세요. 판단 이유: 이 시기에는 프로젝트 자체가 가장 큰 일정인 경우가 많습니다.
```

---

