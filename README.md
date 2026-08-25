# Agreed 백엔드 API

계약 체결 이후 Gmail·Slack 대화에서 새 요구사항을 찾고, 계약 근거와 변경 초안을
사람이 검토하도록 돕는 FastAPI 서버입니다. 프론트는 별도
`young-thirty/agreed_dev` 저장소에 있습니다.

현재 포함된 기반:

- 이름·이메일·비밀번호 회원가입/로그인과 HttpOnly 세션
- MongoDB + Beanie 사용자별 데이터 귀속
- Gmail OAuth와 읽기 전용 메일 조회
- Slack OAuth와 워크스페이스·채널·스레드·파일 조회
- DeepSeek 요구사항 추출과 계약 변경 승인 흐름
- 프로젝트·요청·자료·원문 저장 및 프로젝트별 계약/요구사항 API
- Gmail 상대·Slack 채널 sync와 BackgroundTasks 기반 3색 요청 판정·자료 분류

## 로컬 실행

Python 3.12를 사용합니다.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash scripts/configure_local_env.sh
docker run -d -p 27017:27017 --name agreed-mongo mongo
.venv/bin/uvicorn app.main:app --reload --port 8000
```

로그인한 사용자에게 시연용 빈 프로젝트를 만들려면 Swagger의 `POST /api/projects`를
사용하거나 `python scripts/seed_demo.py --email 가입이메일`을 실행합니다.

설정 스크립트는 Google·Slack secret과 선택적인 DeepSeek 키를 화면에 표시하지
않고 입력받아 `.env`에 저장하며, provider token 암호화 키를 자동 생성합니다.

OAuth 콘솔의 callback은 프론트가 아닌 FastAPI 주소로 등록합니다.

```text
http://localhost:8000/api/email/callback
http://localhost:8000/api/slack/callback
```

- API 문서: http://localhost:8000/docs
- 상태 확인: http://localhost:8000/api/health
- 프론트 연결 안내: [FE_INTEGRATION.md](./FE_INTEGRATION.md)
- 데이터·AI 설계: [DATA_AI_PIPELINE.md](./DATA_AI_PIPELINE.md)
- 최종 화면·DTO·API 설계: [PRODUCT_API_DESIGN.md](./PRODUCT_API_DESIGN.md)

실제 Google·Slack callback 테스트는 각 콘솔에 위 URI와 secret을 등록한 뒤
진행합니다. 운영 배포 주소가 생기면 두 callback을 HTTPS 운영 주소로 한 번 더
등록해야 합니다.

## 작업 문서

| 문서 | 내용 |
|---|---|
| [CLAUDE.md](./CLAUDE.md) | 백엔드 작업 규약 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 기술 구조 |
| [HANDOFF.md](./HANDOFF.md) | 현재 상태와 다음 작업 |
| [DATA_AI_PIPELINE.md](./DATA_AI_PIPELINE.md) | 프로젝트·수집·AI·근거 설계 |
| [PRODUCT_API_DESIGN.md](./PRODUCT_API_DESIGN.md) | UNI 화면별 DTO·API·구현 순서 |
