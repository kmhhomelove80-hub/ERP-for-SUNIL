# Windows 서버 + Synology NAS 실행 준비

현재 파악한 장비: 상시 가동 Windows Server(버전/RAM 미확인), Synology DS1621+(가용 공간 약 10.5TB). NAS 실제 DSM/RAM/부하와 Container Manager 설치 여부는 미확인이다. Windows 실행 + NAS 백업을 기본안으로 준비했으며 NAS 단독 컨테이너 운영은 별도 선택지다.

현재 개발 환경은 Linux다. Waitress HTTP 기동과 NAS 복사 로직은 이 환경에서 검증하며, PowerShell/작업 스케줄러/DPAPI/SMB의 실제 Windows 실행은 아직 검증하지 않았다. 아래 파일은 자동 설치 완료를 의미하지 않는다.

## 먼저 확인할 정보

- Windows Server 버전, Python 3.12 설치 지원 여부, RAM, 여유 디스크, 설치 권한.
- NAS 내부 호스트명/사설 IP, ERP 전용 공유 폴더, 실행 계정의 SMB 읽기/쓰기 권한.
- 실제 ERP 접속 호스트명·HTTPS 경로. 주어진 DSM 5001 관리 주소를 ERP 서비스 주소로 사용하지 않는다.
- DS1621+의 DSM 버전/메모리/Container Manager 상태. 저장공간만으로 컨테이너 실행 여유를 판단하지 않는다.

## 설치 순서(담당자 확인 후)

1. Windows 전용 운영 계정과 PostgreSQL DB/전용 사용자를 준비한다. SQL 암호를 채팅이나 코드에 기록하지 않는다.
2. 소스를 C:\SUNIL\ERP에 복사하고 Python 3.12로 가상환경을 만든다.

```powershell
py -3.12 -m venv C:\SUNIL\ERP\.venv
C:\SUNIL\ERP\.venv\Scripts\python.exe -m pip install -r C:\SUNIL\ERP\requirements.txt
```

3. runtime.example.json을 C:\ProgramData\SUNIL\runtime.json으로 복사하고 실제 경로/호스트/DB 사용자/인증된 NAS UNC 경로를 입력한다. 비밀번호/SECRET_KEY는 JSON에 넣지 않는다. 기본 Listen=127.0.0.1은 같은 PC의 HTTPS 프록시를 전제로 한다. NAS 리버스 프록시를 쓰려면 승인된 내부 IP와 Windows 방화벽 허용 범위를 별도로 설정해야 한다.
4. **작업을 실행할 동일 Windows 계정**으로 Initialize-Secrets.ps1을 실행한다. 로컬에서 DB 암호를 입력하고 앱 키는 무작위로 만든다. SecureString은 Windows DPAPI로 보호되며 디렉터리 ACL도 제한한다. icacls의 SID는 *SID 형식으로 지정한다. 실제 계정 ACL 적용 결과도 확인한다. 실제 값은 로그/채팅에 쓰지 않는다.
5. Common.ps1의 Import-SunilEnvironment로 설정을 적용한 운영 계정 세션에서 migrate, collectstatic, createsuperuser, check_readiness --deployment를 실행한다. 초기 이관은 검증 결과 확인 후 한 번만 적용한다.
6. 먼저 Run-Erp.ps1로 수동 기동한다. 실제 서버의 HTTPS/Caddy 또는 검토된 NAS 리버스 프록시를 연결하고 PC/휴대폰에서 검증한다. 8000 포트를 인터넷에 직접 공개하지 않는다. 같은 PC의 Caddy를 쓸 때는 Windows용 공식 Caddy 실행 파일과 deploy/windows/Caddyfile을 사용한다. 예제 CaddyExe/CaddyConfig와 ERP_PUBLIC_HOST를 실제 경로/호스트로 바꾸고 `caddy validate` 후 Run-Caddy.ps1로 기동한다. 실제 DNS·80/443·인증서 확인 전 HTTPS 준비 완료로 보지 않는다. NAS 리버스 프록시를 선택했다면 Caddy 작업은 등록하지 않는다.

```powershell
. C:\SUNIL\ERP\deploy\windows\Common.ps1
$config = Get-SunilConfiguration -ConfigPath C:\ProgramData\SUNIL\runtime.json
Import-SunilEnvironment -Config $config
Set-Location C:\SUNIL\ERP
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe manage.py check_readiness --deployment
```

스크립트 실행 정책은 회사 정책을 따른다. 무조건적인 ExecutionPolicy Bypass나 전역 정책 해제를 적용하지 않는다.

## 자동 시작/백업 작업

작업 스케줄러에서 아래 작업을 관리자가 등록한다. 비밀번호는 스케줄러의 보안 입력으로 등록하며 스크립트/명령줄에 넣지 않는다.

| 작업 | 트리거 | 실행 |
|---|---|---|
| SUNIL-ERP | 시스템 시작, 네트워크 준비 후 | powershell.exe -NoProfile -NonInteractive -File "C:\SUNIL\ERP\deploy\windows\Run-Erp.ps1" -ConfigPath "C:\ProgramData\SUNIL\runtime.json" |
| SUNIL-HTTPS | 시스템 시작, 네트워크 준비 후 | powershell.exe -NoProfile -NonInteractive -File "C:\SUNIL\ERP\deploy\windows\Run-Caddy.ps1" -ConfigPath "C:\ProgramData\SUNIL\runtime.json" |
| SUNIL-Backup | 매일 02:00(Windows 시간대 한국 확인), 놓친 작업 실행 | powershell.exe -NoProfile -NonInteractive -File "C:\SUNIL\ERP\deploy\windows\Run-Backup.ps1" -ConfigPath "C:\ProgramData\SUNIL\runtime.json" |

운영 계정은 DPAPI 비밀 파일을 만든 계정과 같아야 한다. 로그온 여부와 관계없이 실행, 실패 시 재시작, 동일 작업 중복 실행 금지를 설정한다. 상시 실행되는 SUNIL-ERP/SUNIL-HTTPS는 작업 실행 시간 제한(기본 3일 등)을 해제해 임의 종료되지 않도록 한다. 백업 작업에는 회사 기준의 유한 시간 제한을 두고, 시간 초과/중단 후 로컬 백업 및 NAS 임시 폴더를 점검한다. **SMB NAS 접근에는 네트워크 자격 증명이 필요하므로 ‘암호를 저장하지 않음(로컬 리소스만)’ 방식은 사용하지 않는다.** 매핑 드라이브 문자 대신 UNC 경로를 사용한다. NAS 권한/자격 증명은 해당 계정으로 작업 실행 시 확인한다.

백업은 먼저 로컬에 완료하고 NAS 임시 폴더로 복사한 다음 체크섬이 맞을 때 완료 폴더로 전환한다. NAS 연결 실패 시 로컬 백업은 남고 작업은 실패 상태가 된다. 실패 로그와 다음 성공을 확인한다. 실행 중 DB 파일이나 DPAPI 비밀 파일을 일반 동기화 폴더에 넣지 않는다.

## 인수 검증

- 서버 재부팅 후 사용자 로그온 없이 앱 자동 시작 확인.
- 자동 백업의 실행 결과/로그, NAS 완료 폴더 및 체크섬 확인.
- 새 DB로 복원하고 현재고/예약/최근 이력을 대조.
- NAS 연결을 일시적으로 사용할 수 없는 시험에서 로컬 백업 보존과 실패 표시 확인(실제 장비 시험은 담당자 승인 후).
- 실제 휴대폰, 사내망, 필요한 경우 외부 접속을 각각 확인.
- Windows DPAPI 파일은 다른 계정/컴퓨터에서 그대로 복호화되지 않는다. 복구 시 회사 보관 DB 암호와 새 앱 키로 비밀 파일을 다시 준비한다. DB 백업 자체도 별도 접근 제어가 필요하다.

Windows의 chmod는 POSIX 600/700과 같은 ACL 보장을 제공하지 않는다. NAS 공유 폴더/로컬 DataRoot 권한을 관리자가 실제로 확인해야 한다.

## 서버 정보의 읽기 전용 확인

`Inspect-Server.ps1`은 Windows 버전/빌드, 실제 RAM, 고정 디스크 용량, PowerShell 버전과 도구 존재 여부만 출력한다. 관리자 계정명·IP·암호·환경 변수 전체·장비 일련번호를 출력하지 않으며 설치/설정 변경/NAS 접속을 수행하지 않는다. 회사 스크립트 실행 정책에 따라 검토 후 실행하고 결과를 인수 담당자와 확인한다. 현재 Linux 환경에서는 실제 실행하지 않았다.

```powershell
powershell.exe -NoProfile -File C:\SUNIL\ERP\deploy\windows\Inspect-Server.ps1
```

개발 환경 추가 점검: Microsoft 공식 PowerShell 7.6.9 Linux 배포의 체크섬을 검증한 뒤 위 PS1 6개의 구문 분석을 통과했다. 이는 Windows PowerShell 5.1에서의 실제 실행, DPAPI·ACL·스케줄러·SMB 검증을 대신하지 않는다.
