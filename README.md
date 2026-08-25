# Agreed — 백엔드 API

계약 이후 고객 대화에서 새 요구사항을 찾아, 지금 합의된 계약 상태를 최신으로 유지합니다.

프론트엔드는 별도 저장소(`young-thirty/agreed_dev`, Next.js)에 있습니다.

## 시작

```bash
pip install -r requirements.txt

docker run -d -p 27017:27017 --name agreed-mongo mongo

cp .env.example .env      # DEEPSEEK_API_KEY 채우기

uvicorn app.main:app --reload --port 8000
```

- API 문서: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/api/health

API 키가 없어도 서버는 뜨고 고정 시연 시나리오는 동작합니다.

## 문서

작업 전에 읽으세요.

| 문서 | 내용 |
|---|---|
| [CLAUDE.md](./CLAUDE.md) | 작업 규약. 계층 규칙, AI 설계 원칙, Git 컨벤션 |
| [HANDOFF.md](./HANDOFF.md) | 인수인계. 기획 의도, 시연 시나리오, 확정된 결정, 남은 일 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 기술 설계 근거 |
