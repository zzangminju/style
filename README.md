# 맞춰봄 — AI 코디 추천 웹서비스

가진 옷과 갈 자리를 입력하면, AI가 그 자리에 어울리는 코디를 제안해주는 웹서비스입니다.
"유행"이 아니라 "어울림"을 기준으로 스타일링을 도와줍니다.

## 기능

- **AI 코디 추천**: 사용자가 가진 옷(예: 베이지 트렌치, 청바지)과 상황(예: 소개팅)을 입력하면 OpenAI가 코디를 제안
- **3개 페이지**: Cover(소개) / Styling(추천) / Gallery(상황별 룩)
- **반응형**: 모바일·태블릿·데스크톱 대응

## 기술 구성

- **프론트엔드**: 순수 HTML / CSS / JavaScript (`index.html`)
- **백엔드**: Vercel 파이썬 서버리스 함수 (`api/recommend.py`)
- **AI**: OpenAI Chat Completions API (`gpt-4o-mini`)

## 구조

```
.
├── index.html          # 프론트엔드 (3페이지 SPA)
├── api/
│   └── recommend.py    # 서버리스 함수: 입력을 받아 AI 코디를 반환
└── README.md
```

## 환경변수

배포 시 Vercel 프로젝트 설정에 아래 환경변수를 등록해야 합니다.

| 이름 | 설명 |
|------|------|
| `OPENAI_API_KEY` | OpenAI API 키 (`sk-`로 시작) |

## 동작 원리

1. 사용자가 옷과 상황을 입력하고 "Get the look" 클릭
2. 브라우저가 `/api/recommend`로 입력을 POST
3. 서버리스 함수가 서버에 저장된 API 키로 OpenAI에 요청 (키는 브라우저에 노출되지 않음)
4. AI가 만든 코디 제안을 화면에 표시
