# SUNIL 창고 관리

회사 ERP의 부산(Y09) 로케이션 관리를 보완하는 Django 웹 프로그램. 영주(Y02)는 본사 참고 수량이다. PC와 휴대폰에서 재고·입고·피킹·출고·이동·전산 대조를 사용한다. AS는 제외한다.

현재 핵심 기능 구현/개발 검증을 완료했으며 운영 서버에는 배포하지 않았다. 회사 서버·호스트명·후임자·최종 자료를 확인한 뒤 전환한다.

- [Windows 노트북·같은 Wi-Fi 휴대폰 테스트](docs/LAPTOP_TEST.md)
- [개발계획](docs/DEVELOPMENT_PLAN.md)
- [배치별 진도](docs/PROGRESS.md)
- [검증 결과와 남은 조건](docs/ACCEPTANCE.md)
- [사용·인수인계 안내](docs/USER_GUIDE.md)
- [현장 인수 검증표·남은 일정](docs/HANDOVER_CHECKLIST.md)
- [운영·백업·복구](deploy/README.md)

## 로컬 개발 실행

Python 3.12에서 검증했다. 기존 checkout을 사용하며 별도 worktree가 필요하지 않다.

```bash
cd /workspace/ERP-for-SUNIL
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

/login/에서 로그인한다. 기본 DB는 개발용 SQLite이며 운영 데이터/사용자는 자동으로 생성하지 않는다. 관리자에서 품목을 등록하거나 초기 이관한다. 업무 데이터는 전용 화면에서 변경하고 관리자 직접 편집은 막았다.

PostgreSQL에는 ERP_DB_NAME, ERP_DB_USER, ERP_DB_PASSWORD 및 선택적으로 ERP_DB_HOST/PORT/SSLMODE를 실행 환경에서 지정하고 migrate한다. 암호를 저장소에 기록하지 않는다. 운영에는 PostgreSQL과 HTTPS를 사용하며 개발 runserver를 공개하지 않는다.

## 주요 화면

| 경로 | 기능 |
|---|---|
| / | 재고 검색/상태 필터/합계/모바일 카드 |
| /receipts/ | 입고 원거래와 취소 |
| /receipts/new/ | 입고·예정 등록 |
| /receipts/bulk/ | A~E열 붙여넣기/Excel 일괄 입고 |
| /picking/new/ | 수량·ERP 코드 또는 고객 6열 입력/배정/부족표 |
| /allocations/ | 일반 배정 프로젝트·SEQ 필터/Shift 선택/필터 전체 Release·취소 |
| /substitutions/ | 대체 출고(빌림)·생산 입고분 상환·회사 ERP 정산 확인 |
| /movements/ | 본인 이동 요청/관리자 처리 |
| /erp/ | ERP 자료 업로드·대조·원인/조치 |
| /history/ | 새 시스템 작업 이력 |
| /archives/ | 기존 Excel 과거 이력 보관 |

staff 관리자는 거래·전체 요청·대조·이력을 관리한다. 일반 사용자는 재고 조회와 본인 이동 요청/결과를 사용한다.

## 최초 데이터 이관

원본 파일은 checkout 밖에 보관한다. xlsm 매크로/수식은 실행하지 않는다. 먼저 기본 검증만 수행하고, 출력 수량과 경고/예정 위치를 확인한 뒤 빈 재고 DB에만 적용한다.

```bash
.venv/bin/python manage.py import_legacy /path/to/source.xlsm \
  --expected-location '검토한 예정 위치'
.venv/bin/python manage.py import_legacy /path/to/source.xlsm \
  --expected-location '검토한 예정 위치' --apply --actor 관리자사용자명
.venv/bin/python manage.py import_legacy_history /path/to/source.xlsm
.venv/bin/python manage.py import_legacy_history /path/to/source.xlsm --apply --actor 관리자사용자명
.venv/bin/python manage.py import_legacy_history /path/to/SEARCH_PHONE.xlsx --apply --actor 관리자사용자명
```

현재고/활성 예약과 과거 이력은 분리한다. 같은 원본 해시의 재적용은 중복되지 않으며, 다른 파일로 같은 과거 이력 종류를 증분 이관하는 것은 차단한다. 과거 이력을 가져와도 재고를 다시 움직이지 않는다. 예정 위치를 임의 추정하지 않으며 첨부 파일의 ‘9/10 입고’ 상태는 운영 전 확인해야 한다.

회사 ERP 자료는 /erp/의 표준 양식을 사용하거나 기존 재고현황(I:R=Y02/W:AF=Y09) 배치를 확인해 업로드한다. 비교 자료는 현재고를 덮어쓰지 않으며 원본 음수를 보존한다.

## 검증

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test --noinput
.venv/bin/python scripts/smoke_http.py --gunicorn
# 현재 검증 환경에 준비한 격리 PostgreSQL 17 바이너리
.venv/bin/python scripts/run_postgres_tests.py /workspace/.local/postgresql
```

PostgreSQL 스크립트는 임시 Unix 소켓 DB에서 테스트 후 새 DB 백업 복원을 대조하고 모두 제거한다. 외부 TCP나 운영 DB는 만들지 않는다. 바이너리가 없다면 공식 검증된 PostgreSQL 설치를 준비하거나 ERP_DB_*를 별도 테스트 DB로 지정한다.

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/smoke_browser.py --chromium /usr/bin/chromium --artifacts /tmp/sunil-browser-artifacts
```

브라우저 검증은 가상 데이터/임시 계정/DB만 사용한다. 현재 환경에서는 기본 Python의 Playwright 1.62.0과 Chromium으로 실행했다. PNG/PDF/집계 결과만 남기고 서버와 DB를 제거한다. PDF는 브라우저 인쇄 저장이며 서버 자동 PDF 보관 기능은 아니다.

GitHub Actions 설정은 SQLite/PostgreSQL 테스트용으로 작성했으며 아직 원격에서 실행하지 않았다. 실제 데이터·백업·암호·원본 Excel을 원격 저장소에 올리지 않는다.

## 회사 Windows 서버 / Synology NAS 준비

현재 운영 후보는 상시 가동 Windows Server와 Synology DS1621+다. Windows에서는 Waitress를 사용하며 Gunicorn 설치는 플랫폼 조건으로 제외한다. [Windows 실행·예약 백업 안내](deploy/windows/README.md)에 비밀값 없는 설정 예제, DPAPI 준비, 자동 시작/HTTPS/백업 작업 등록 절차를 정리했다. 실제 Windows 버전·메모리·NAS 공유 권한은 아직 확인 전이다.

```bash
# 현재 개발 환경에서 Waitress 앱 기동 검증
.venv/bin/python scripts/smoke_http.py --waitress
# 가상 데이터로 전체 브라우저 흐름과 운영 점검 화면 확인
python scripts/smoke_browser.py --waitress --artifacts /tmp/sunil-waitress-artifacts
# DB/마이그레이션/원장 예약 합계 읽기 전용 점검
.venv/bin/python manage.py check_readiness
# 운영 모드/DB/HTTPS 설정까지 점검
.venv/bin/python manage.py check_readiness --deployment
# 로컬 백업 후 별도 연결된 저장소로 검증 복사
.venv/bin/python manage.py backup_to_storage --directory /path/to/local-backups --storage /path/to/connected-storage --pg-bin /path/to/postgresql/bin
```

운영 점검은 /operations/의 관리자 화면에서도 확인한다. 읽기 전용이며 실제 인증서·Windows 작업 실행·NAS 접근/복원을 대신하지 않는다. 위 별도 저장소 복사는 존재하는 경로만 사용하고, 임시 복사→체크섬→완료 폴더 전환을 거친다. NAS 실패 시 로컬 백업을 보존한다. Windows에서는 UNC 경로와 실행 계정의 SMB 권한이 필요하다.

## 거래 기록 보존·엑셀 출력

- /picking/은 완료된 주문까지 다시 찾아 인쇄할 수 있는 목록이다. /allocation-history/는 기존 SEQ를 포함한 예약/출고/취소 행 이력이다.
- 신규 거래는 배정 당시 상세와 출고·취소 당시 상세/처리자를 보존한다. 완료 기록은 나중에 바뀐 품목 설명을 따라 바뀌지 않는다. 예전 미저장 상세를 추정해 채우지 않는다.
- 재고/ERP 대조 화면에서 검색 조건 전체를 조회용 xlsx로 내보낸다. 최대 50,000행, 텍스트의 수식 변환 방지, 음수/누락 보존을 검증했다.
- deploy/windows/Inspect-Server.ps1은 실제 Windows 사양을 설치/변경 없이 확인하기 위한 준비 스크립트다. 실행 정책은 회사 기준을 따르며 현재 Linux에서 Windows 실행 검증을 주장하지 않는다.
