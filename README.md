# TodayCommunity

This repository is a one-way public deployment mirror. Private project notes, Git history, and credentials are not included.

TodayCommunity는 커뮤니티를 반복해서 새로고침하는 대신, 정해 둔 조건을 만족한 글의 목록 정보만 모아 보는 개인용 아카이브입니다.

- 서비스: [todaycommunity.pages.dev](https://todaycommunity.pages.dev)
- 현재 대상: 디시인사이드 특이점이 온다 갤러리
- 운영 주체: 개인 프로젝트이며 디시인사이드와 제휴하거나 공식적으로 연계된 서비스가 아닙니다.

## 수집 범위

목록 페이지에 공개된 글 번호, 말머리, 제목, 원문 링크, 작성 시각, 추천 수와 댓글 수를 저장합니다. 게시글 본문, 이미지, 작성자 닉네임과 IP는 수집하지 않습니다.

수집 요청은 직렬로 보내고 요청 사이의 최소 간격을 둡니다. 원본 사이트가 차단 상태를 반환하면 해당 실행을 즉시 중단하고 쿨다운을 적용합니다. 최신 글 확인과 과거 데이터 정리는 서로 다른 시간 예산으로 실행됩니다.

## 구성

- `crawler/`: 목록 파싱, 선별, D1 저장과 재개 상태 관리
- `dashboard/`: 현재 Cloudflare Pages 공개 화면
- `functions/api/`: D1을 읽는 Pages Function
- `.github/workflows/`: GitHub Actions crawl and Cloudflare deployment workflows

## 로컬 검증

Python 테스트:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Node 테스트:

```powershell
node --test tests/archive_api.test.mjs tests/dashboard_contract.test.mjs tests/scheduler_worker.test.mjs
```

로컬 D1 접근이 필요한 경우 `.env.example`을 참고해 Git에서 제외되는 `.env.local`에 값을 설정합니다. 실제 계정 ID, 데이터베이스 ID와 API token은 소스·문서·로그에 기록하지 않습니다.

## 라이선스와 데이터

이 저장소에는 별도의 오픈소스 라이선스가 부여되어 있지 않습니다. 원문 링크와 제3자 게시물 메타데이터에 관한 권리는 각 원저작자와 서비스 운영자에게 있습니다.

저장된 목록 메타데이터에는 현재 자동 만료 정책이 없으며, 원문 삭제가 즉시 반영된다고 보장하지 않습니다. 운영 과정에서 보존·삭제 정책을 별도로 정비할 예정입니다.

보안상 민감한 문제는 공개 이슈에 내용을 남기지 말고 [비공개 보안 제보](https://github.com/Chimnii/TodayCommunity-Public/security/advisories/new)를 이용해 주세요.
