# 운영 구성 초안 — 아직 배포하지 않음

승인된 회사 소유 서버/계정과 호스트명이 준비되면 적용할 단일 서버 구성이다. 현재 파일은 설치/배포를 수행하지 않는다. 비용 결제·공개 게시·DNS 변경은 별도 승인 후 진행한다.

## 구성

- Django + Gunicorn(1 worker, 2 threads), 로컬 PostgreSQL, Caddy HTTPS.
- 외부에는 HTTPS만 제공하고 Gunicorn은 127.0.0.1, PostgreSQL은 로컬 연결만 사용한다.
- 최소 1GB RAM을 시작 기준으로 잡되 실제 파일 크기/메모리 측정 후 결정한다. 공급자/요금은 아직 선정·확인하지 않았다.
- 앱/서비스 계정은 `sunil`, checkout `/srv/sunil/app`, 비공개 데이터/백업 `/var/lib/sunil`.

## 적용 순서

1. 회사 소유 서버와 도메인/호스트명, 인수 담당자를 확정한다. SSH 관리 계정과 복구 수단도 회사가 보유한다.
2. 서버에 지원되는 Python, PostgreSQL, Caddy와 전용 OS/DB 사용자를 준비한다. Ubuntu/Debian 배포판별 공식 설치 지침을 사용한다.
3. 프로젝트와 requirements.txt로 venv를 설치한다. 운영 계정으로 읽을 수 있는 `/etc/sunil.env`를 만들고 `environment.example`의 자리표시자를 실제 값으로 대체한다. 파일을 저장소에 넣지 않는다.
4. 환경 변수를 적용한 서비스 계정으로 `manage.py migrate`, `manage.py collectstatic --noinput`, `manage.py check --deploy`, `manage.py createsuperuser`를 실행한다. 계정 암호는 안전한 방식으로 정하고 채팅/로그에 쓰지 않는다.
5. `sunil.service`를 설치한다. Caddy 서비스의 환경에서 `ERP_PUBLIC_HOST`를 실제 호스트명으로 설정하고 Caddyfile을 검증한 뒤 적용한다. DNS와 80/443 연결이 준비되어야 인증서 발급을 확인할 수 있다.
6. 승인된 실데이터 파일의 초기 이관/이력 이관/ERP 자료를 검증하고 반영한다. 예전 원본과 적용 전 DB 백업은 별도 보존한다.
7. 회사 PC 및 실제 휴대폰/외부망에서 로그인·입고·피킹·Release·이동·ERP 대조를 확인한다. 확인 전 기존 Excel 운영을 중단하지 않는다.
8. `sunil-backup.service`와 `.timer`를 설치하고 실제 백업/복원 확인 후 활성화한다.

## 백업·복원

```bash
# PostgreSQL에서는 pg_dump가 PATH에 있어야 한다.
.venv/bin/python manage.py backup_database --directory /var/lib/sunil/backups
# 체크섬 확인만 수행한다.
.venv/bin/python manage.py restore_database /path/to/one-backup
# SQLite는 존재하지 않는 새 파일에만 복원한다.
.venv/bin/python manage.py restore_database /path/to/sqlite-backup --apply --sqlite-target /path/to/new.sqlite3
# PostgreSQL은 ERP_DB_*가 가리키는 새 빈 DB에만 복원한다.
.venv/bin/python manage.py restore_database /path/to/pg-backup --apply
```

각 백업은 별도 디렉터리(700), DB와 manifest(600)로 생성한다. SHA-256은 손상 검출용이며 암호화나 위변조 방지 서명을 대신하지 않는다. 백업에는 업무 데이터와 사용자 암호 해시가 포함된다. 회사 OneDrive/SharePoint의 접근 제한 폴더에는 **완료된 백업 디렉터리**를 복사하고, 실행 중 DB 파일을 동기화하지 않는다. 클라우드 동기화 계정/연결 방식은 아직 구성하지 않았다.

초기에는 매일 백업을 14일 이상 보관하고 주 단위 복원 확인을 권한다. 자동 삭제는 아직 넣지 않았으므로 용량을 확인하고 복구 검증된 백업만 보존 정책에 따라 정리한다. 복원은 별도 DB에서 먼저 확인한 뒤 운영 연결을 전환하는 절차로 진행하며, 현재 명령은 기존 DB 덮어쓰기를 차단한다.

## 장애 시

- 서비스 상태/로그를 확인하고 원본 Excel·직전 백업을 보존한다.
- 새 DB로 복원해 재고/예약/최근 거래를 확인한다.
- 운영 환경 파일을 새 DB로 전환하는 것은 담당자 승인과 점검 후 수행한다.
- 서버가 달라지면 DB 이외에 환경 변수/도메인/인증서/회사 저장공간 연결도 다시 준비해야 한다.

## 보관 백업의 정기 점검

```bash
# DB 접속 없이 완료 백업 전체의 체크섬과 최신성을 확인한다.
.venv/bin/python manage.py check_backups /path/to/backup-storage --max-age-hours 26
```

Windows에서도 동일하게 `python.exe manage.py check_backups '\\NAS\share\sunil'`로 실행한다. JSON 결과와 성공 종료 코드 0을 함께 확인한다. 경로 접근 불가, 완료 백업 없음, 최신 정상 백업 26시간 초과, 손상/형식 오류, 복사 임시 폴더가 있으면 실패 종료한다. 저장소 최상위의 `sunil-*` 완료 폴더를 대상으로 모든 DB 파일을 읽으므로 백업 용량이 크면 시간이 걸린다. 백업 복사 실행 중에는 임시 폴더가 표시될 수 있어 완료 뒤 다시 확인한다. 파일을 수정하거나 삭제하지 않고, DB 복원/SQLite 내부 구조/PostgreSQL dump 내용까지 검증하지 않는다. 후임자가 수행할 실제 복원·전환 검증은 [인수 검증표](../docs/HANDOVER_CHECKLIST.md)를 따른다.
