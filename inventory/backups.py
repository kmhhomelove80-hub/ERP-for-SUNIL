"""Consistent database backups; restoration never overwrites existing data."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime,timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection

def _sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def _pg_program(name,pg_bin):
    executable=name+'.exe' if sys.platform=='win32' else name
    value=str(Path(pg_bin)/executable) if pg_bin else shutil.which(executable)
    if not value or not Path(value).is_file(): raise ValidationError(f'{name} 실행 파일이 필요합니다. --pg-bin 경로를 지정하세요.')
    return value

def _pg_env():
    db=settings.DATABASES['default'];env=os.environ.copy()
    env.update(PGHOST=str(db.get('HOST') or 'localhost'),PGPORT=str(db.get('PORT') or '5432'),PGUSER=str(db.get('USER') or ''),PGDATABASE=str(db['NAME']),PGPASSWORD=str(db.get('PASSWORD') or ''))
    # Preserve inherited TLS settings. Honor Django's explicit SSL choice too.
    for option in ['sslmode','sslrootcert','sslcert','sslkey']:
        if option in db.get('OPTIONS',{}):env['PG'+option.upper()]=str(db['OPTIONS'][option])
    return env

def _run_pg(args):
    result=subprocess.run(args,env=_pg_env(),capture_output=True,text=True)
    if result.returncode:
        # Do not print environment, credentials or full connection errors.
        raise ValidationError(f'PostgreSQL 백업/복원 도구가 종료 코드 {result.returncode}로 실패했습니다.')

def create_backup(directory,pg_bin=None):
    vendor=connection.vendor
    if vendor not in ['sqlite','postgresql']:raise ValidationError('지원하지 않는 DB입니다.')
    directory=Path(directory).expanduser().resolve()
    directory.mkdir(parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime('sunil-%Y%m%dT%H%M%SZ-')+secrets.token_hex(4)
    target=directory/name;target.mkdir(mode=0o700)
    database=target/('database.sqlite3' if vendor=='sqlite' else 'database.dump')
    try:
        if vendor=='sqlite':
            connection.ensure_connection()
            destination=sqlite3.connect(database)
            try:
                connection.connection.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValidationError('SQLite 무결성 확인 실패')
            finally:destination.close()
        else:
            _run_pg([_pg_program('pg_dump',pg_bin),'--format=custom','--no-owner','--file',str(database)])
        database.chmod(0o600)
        manifest=dict(format_version=1,vendor=vendor,database_file=database.name,sha256=_sha256(database),created_at=datetime.now(timezone.utc).isoformat())
        manifest_path=target/'manifest.json';manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2));manifest_path.chmod(0o600)
        return target
    except Exception:
        shutil.rmtree(target)
        raise

def verify_backup(directory):
    directory=Path(directory).expanduser().resolve()
    try:manifest=json.loads((directory/'manifest.json').read_text())
    except (OSError,ValueError,UnicodeError) as error:raise ValidationError('백업 manifest를 읽을 수 없습니다.') from error
    if not isinstance(manifest,dict) or manifest.get('format_version')!=1 or manifest.get('vendor') not in ['sqlite','postgresql']:
        raise ValidationError('지원하지 않는 백업 형식입니다.')
    expected_name='database.sqlite3' if manifest['vendor']=='sqlite' else 'database.dump'
    if manifest.get('database_file')!=expected_name:raise ValidationError('백업 파일명이 유효하지 않습니다.')
    database=directory/expected_name
    if (directory/'manifest.json').is_symlink() or database.is_symlink():
        raise ValidationError('심볼릭 링크 백업 파일은 검증하지 않습니다.')
    if not database.is_file() or _sha256(database)!=manifest.get('sha256'):
        raise ValidationError('백업 체크섬이 일치하지 않습니다. 복원을 중단합니다.')
    return database,manifest

def restore_backup(directory,*,sqlite_target=None,pg_bin=None):
    database,manifest=verify_backup(directory)
    if manifest['vendor']=='sqlite':
        if not sqlite_target:raise ValidationError('--sqlite-target 새 파일 경로가 필요합니다.')
        target=Path(sqlite_target).expanduser().resolve()
        target.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive create is deliberate: a live database must never be replaced.
        try:fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError as error:raise ValidationError('기존 파일에는 복원하지 않습니다. 새 파일 경로를 지정하세요.') from error
        os.close(fd)
        try:
            source=sqlite3.connect(f'{database.as_uri()}?mode=ro',uri=True)
            dest=sqlite3.connect(target)
            try:
                source.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValidationError('복원 DB 무결성 확인 실패')
            finally:source.close();dest.close()
        except Exception:
            target.unlink(missing_ok=True);raise
        return str(target)
    if connection.vendor!='postgresql':raise ValidationError('PostgreSQL 대상 연결 설정이 필요합니다.')
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f')")
        if cursor.fetchone()[0]:raise ValidationError('비어 있는 새 PostgreSQL DB에만 복원할 수 있습니다.')
    _run_pg([_pg_program('pg_restore',pg_bin),'--exit-on-error','--single-transaction','--no-owner','--no-privileges','--dbname',str(settings.DATABASES['default']['NAME']),str(database)])
    return '새 PostgreSQL DB 복원 완료'

def copy_backup_to_storage(backup_directory,storage_directory):
    """Copy a finished backup, verify it, then expose the completed directory.

    The storage must already exist (e.g. an authenticated NAS UNC share). A
    failure leaves the verified local backup intact; no retention/deletion runs.
    """
    import tempfile
    database,manifest=verify_backup(backup_directory)
    source=Path(backup_directory).expanduser().resolve()
    storage=Path(storage_directory).expanduser().resolve()
    if not storage.is_dir():raise ValidationError('NAS/백업 저장 경로가 연결되어 있지 않습니다.')
    if source==storage or storage in source.parents or source in storage.parents:
        raise ValidationError('원본 백업과 별도의 저장 경로를 사용하세요.')
    target=storage/source.name
    if target.exists():
        _,existing=verify_backup(target)
        if existing!=manifest:raise ValidationError('같은 이름의 다른 백업이 있습니다. 덮어쓰지 않습니다.')
        return target
    staged=Path(tempfile.mkdtemp(prefix='.sunil-partial-',dir=storage))
    try:
        for name in [manifest['database_file'],'manifest.json']:
            original=source/name
            if original.is_symlink():raise ValidationError('심볼릭 링크 백업 파일은 복사하지 않습니다.')
            shutil.copyfile(original,staged/name)
            (staged/name).chmod(0o600)
        verify_backup(staged)
        try:staged.rename(target)
        except OSError:
            if not target.is_dir():raise
            _,existing=verify_backup(target)
            if existing!=manifest:raise ValidationError('다른 백업을 덮어쓰지 않습니다.')
        return target
    finally:
        if staged.exists():shutil.rmtree(staged)
