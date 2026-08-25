# 인수인계 프롬프트

> 아래 블록을 그대로 복사해 다른 LLM에게 붙여넣으면 바로 작업을 이어받는다.

---

```
너는 Agreed 백엔드(young-thirty/agreed_be)를 이어받아 작업한다.
FastAPI + MongoDB(Beanie) + DeepSeek 기반이고, 내일 시연이 목표다.

## 제품이 하는 일

프리랜서가 계약 체결 이후 클라이언트와 Gmail·Slack에서 주고받는 대화에서
새로 생긴 요구사항을 찾아내고, 계약 변경분을 사람이 승인하도록 돕는다.

핵심 원칙 하나만 기억하면 된다.
"AI는 무엇이 바뀌었는지 관리하고, 사람은 바꿔도 되는지 판단한다."

금액·일정·수락 여부는 사람이 정한다. 계약을 바꾸는 함수는
core/contract_ops.py의 apply_to_contract 하나뿐이고 그 안에 합의 여부 검사가
있다. 이 검사를 우회하는 경로를 만들지 마라.

## 먼저 읽을 것

저장소 루트의 문서를 이 순서로 읽어라.

1. CLAUDE.md          작업 규약. 절대 규칙이 여기 있다
2. DOMAIN_SPEC.md     도메인 모델과 정책 결정. 10절이 남은 작업 순서다
3. AI_AGENTS.md       에이전트 구조, 하네스, 도구
4. USER_FLOW.md       화면별 흐름과 아직 연결 안 된 구간
5. PRODUCT_API_DESIGN.md   확정 DTO

프론트는 별도 저장소 young-thirty/agreed_dev (Next.js, dev 브랜치)다.
백엔드는 API만 담당한다. 화면 코드를 이 저장소에 두지 마라.

## 절대 규칙

- core/ 에는 fastapi, beanie, pymongo, openai, os.environ 이 등장하면 안 된다.
  순수 함수와 값 객체만 둔다. core/ 가 models/ 를 import 하지 않는다.
- Beanie Document 인스턴스를 모듈 로드 시점에 만들지 마라. init_beanie 전에는
  CollectionWasNotInitialized가 나서 앱이 아예 뜨지 않는다.
- 값 객체가 앞, Document가 뒤다:  class Contract(ContractState, Document)
- 응답은 { "ok": true, "data": ... } 또는 { "ok": false, "error": "한국어 문장" }.
  app/response.py 의 ok() / fail() 을 써라. error에 스택 트레이스를 넣지 마라.
- 필드 이름은 camelCase다. 프론트가 그 이름을 기대한다. 파이썬답지 않아 보여도
  바꾸지 마라.
- 모든 조회·수정에 세션에서 얻은 ownerId 조건을 포함한다. 요청 body의
  사용자 ID를 신뢰하지 마라.
- 주석과 커밋 메시지는 한국어로 쓴다.
- 기존 파일을 통째로 다시 쓰지 마라. 필요한 부분만 고쳐라.

## AI 호출 규칙

모든 LLM 호출은 infra/llm/harness.py 를 거친다. 직접 openai 클라이언트를
부르지 마라. 라우트 파일에서 LLM을 부르지 마라(계층 규칙 위반이다).

- run_json(system_prompt, user_content, schema)  단발 JSON mode. 1회 재시도
- run_agent(system_prompt, task, tools, schema)  도구 호출 루프

도구가 필요 없는 판단에 run_agent를 쓰지 마라. 원문 하나 보고 요약하는 일에
도구 루프를 돌리면 토큰과 시간만 쓴다.

프롬프트는 코드에 문자열로 박지 말고 infra/llm/prompts.py 에 모아라.

실패는 예외가 아니라 None으로 돌아온다. 호출부가 안전한 쪽으로 강등한다.
DEEPSEEK_API_KEY가 없어도 화면이 비지 않아야 한다.

모델이 낸 인용문은 코드가 원문과 다시 대조한다(core/grounding.py).
지어낸 인용이면 근거를 버리고 판정을 주황으로 내린다. 부분 수용이 원칙이다 —
5건 중 1건이 실패하면 그 1건만 버리고 4건은 살린다.

## 지금까지 된 것

- 이메일·비밀번호 로그인, HttpOnly opaque 세션
- Gmail·Slack OAuth 연동과 조회, provider token은 Fernet 암호화 저장
- 프로젝트 CRUD, 상태 전환(DRAFT/ACTIVE/COMPLETED)
- source-link 등록 + sync → SourceMessage 저장 → BackgroundTasks 분석
- 요청 다건 추출 → 계약 대조 서브 에이전트 → 3색 판정
  (IN_SCOPE_ACTION_REQUIRED / OUT_OF_SCOPE_COORDINATION_REQUIRED / EXTRA_REQUEST)
- 빨강 판정만 Requirement(9상태 합의 흐름)로 승격 → 계약 반영
- 티켓 상태 3종(active/done/rejected), 사람만 전이
- 인바운드 → 기존 티켓 매칭 (임베딩 없이 컨텍스트로)
- 티켓 솔루션 패키지: 조언·이유·근거 조문·관련 파일
- 답변 초안 말투 레지스트리 (REPLY_STYLES dict, 등록된 키만 허용)
- Git 저장소 탐색 서브 에이전트, 사용자별 GitHub PAT
- Slack 첨부 → S3 저장 (Terraform으로 버킷·IAM 구성)

## 다음에 할 일 (우선순위 순)

### 1. 파일을 티켓에도 할당한다  ← 가장 먼저

지금 ProjectMaterial은 projectId만 갖는다. 파일은 프로젝트 단위로도, 그
안의 티켓 단위로도 할당되어야 한다. 솔루션의 근거·관련 파일·AI 정리 문장이
전부 이 연결을 쓴다.

- models/project_material.py 에 ticketId: PydanticObjectId | None 추가
- 인덱스 (ownerId, ticketId) 추가
- 지금 app/api/projects.py 의 solution 라우트는 프로젝트의 최근 자료 5개를
  그냥 붙인다. 티켓에 할당된 파일이 있으면 그것을 우선하도록 고쳐라
- GET /projects/{id}/materials 에 ticketId 필터를 받게 하라
- 파일마다 AI가 정리한 한두 문장(요약)을 저장할 자리도 필요하다.
  ProjectMaterial에 summary 필드를 더하고 분류 시점에 함께 만들어라

### 2. 남은 도메인 정책 (DOMAIN_SPEC.md 10절 5~6단계)

- 프로젝트 후보 판단(4.2): 클라이언트 메일이 여러 프로젝트에 걸릴 때
  후보가 하나여도 항상 LLM에 묻는다. 후보 목록 밖의 id는 무효 처리
- Draft → Active/Rejected 전환(5절): LLM이 판단한다. 트리거는 draft
  프로젝트에 inbound 또는 outbound가 들어오는 두 경우뿐이다.
  전환 이력과 근거를 남겨 사람이 되돌릴 수 있게 하라
- manual_review 큐(7.2): 별도 화면 말고 대시보드 '확인 필요'에 합친다
- projectClassification(draft/active/none) 필드
- projectStatus에 rejected 추가

### 3. 프론트 연결 (USER_FLOW.md 7절)

- 프론트 /signup이 아직 localStorage에만 저장하고 /api/auth/signup을
  부르지 않는다. 백엔드 API는 그대로 두고 프론트가 붙일 예정이다
- 연동 화면의 Gmail·Slack 조회는 provider 직접 읽기라 저장하지 않는다.
  프로젝트에 원문을 쌓는 것은 source-links + sync다. 이 둘을 헷갈리지 마라
- 파일 업로드 카드가 '사용 가능'인데 업로드 endpoint가 없다

## 실제로 겪은 함정 (같은 실수 반복하지 마라)

1. AnalysisRun의 unique key가 (ownerId, targetType, inputHash, promptVersion)
   이다. 파이프라인을 바꾸면 app/api/projects.py의
   CLIENT_REQUEST_PROMPT_VERSION을 올려라. 안 그러면 이전 결과가 캐시로
   재사용되어 새 코드가 아예 안 돈다. 현재 값은 "v3-ticket-match".

2. run_json의 temperature 기본값은 0이다. 지정하지 않으면 DeepSeek 기본값
   1.0으로 돌아 같은 대화에서 추출 결과가 0건과 1건 사이를 오간다.
   실측으로 확인한 값이다.

3. LLM 클라이언트는 timeout 8초, max_retries=0이다. SDK 재시도와 타임아웃이
   겹치면 최악 24초가 된다. 재시도는 하네스가 1회만 한다.
   서브 에이전트는 전체 예산 30초, 최대 6턴이다.

4. 기존 티켓에 인바운드를 붙일 때 제목과 판정을 덮어쓰지 마라. 티켓의
   정체성은 처음 만들어진 요청이 정한다. 덮어쓰면 "로고 색 변경" 티켓이
   마지막 메시지 제목으로 바뀌어 사람이 추적을 잃는다.

5. 재분석에서 티켓 수가 줄면 앞선 분석이 남긴 카드를 지워야 한다. 단
   매칭된 티켓은 ordinal을 소비하지 않으므로 new_ordinal 카운터를 따로
   써라. 안 그러면 멀쩡한 티켓이 지워진다.

6. 사람이 done으로 바꾼 티켓이 재분석 때문에 다시 active가 되면 안 된다.
   _save_client_requests의 values dict에 ticketStatus를 넣지 마라.

7. Gmail은 사용자당 초당 250 quota unit이고 messages.get이 건당 5 unit이다.
   100개를 한꺼번에 던지면 429가 온다. 동시 요청은 10개로 제한되어 있고,
   실패한 건은 건너뛰고 나머지를 돌려준다. 401만 GmailAuthError로 올려
   재연동 필요와 일시적 실패를 구분한다.

8. FastAPI 최신 버전은 라우터를 _IncludedRouter로 지연 보관한다.
   app.routes를 직접 순회하면 라우트가 안 보인다. app.openapi()['paths']를
   써라.

9. app/api/projects.py 하나가 프로젝트·수집·요청·계약 네 묶음을 담고 있다.
   Swagger 태그는 라우터가 아니라 경로마다 붙인다. 라우터 레벨로 묶으면
   전부 한 덩어리로 보인다.

10. Beanie 2.x는 motor가 아니라 pymongo.AsyncMongoClient를 쓴다.
    인터넷 예제 대부분이 AsyncIOMotorClient인데 그건 1.x다.

## 검증 방법

로컬에 MongoDB와 DEEPSEEK_API_KEY가 있으면:

    python3.12 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/uvicorn app.main:app --reload --port 8000

없으면 최소한 이것만이라도 돌려라. CI가 쓰는 검증과 같다.

    .venv/bin/python -m compileall -q app core infra models scripts
    .venv/bin/python -c "from app.main import app; print(len(app.openapi()['paths']))"

순수 함수는 DB 없이 직접 돌려서 확인할 수 있다. 근거 검증, 상태 전이,
후보 목록 필터링 같은 것들이다.

보안 검토와 광범위한 테스트는 지금 단계에서 생략한다. 시연이 목표다.
다만 다음 둘은 예외로 지켜라.
- 계약 반영 승인 게이트(apply_to_contract)
- 사용자 입력이 프롬프트 지시문 자리에 들어가지 않게 하는 것
  (등록된 키만 받고, 모르는 값은 400으로 거절)

## Git

main이 기본 브랜치다. 지금은 main에 직접 커밋한다.
여러 명이 같은 브랜치에 커밋하므로 푸시 전에 반드시 다시 확인하라.

    git fetch origin main && git merge origin/main
    git push origin main

--force는 쓰지 마라. 커밋 메시지 형식은 "타입: 요약"이고 타입은
feat / fix / chore / docs 다. 요약은 한국어로 쓴다.

main에 푸시하면 GitHub Actions가 자동으로 ECR 빌드 → App Runner 배포까지
한다. 배포 주소는 https://nncjwb3g74.ap-northeast-1.awsapprunner.com 이고
Swagger는 /docs 다.
```
