"""Read-only storage checks: checksums and backup age, without DB access."""
from datetime import datetime, timezone
from pathlib import Path

from django.core.exceptions import ValidationError

from .backups import verify_backup


def backup_health(directory, *, max_age_hours=26, now=None):
    if max_age_hours <= 0:
        raise ValidationError('허용 시간은 0보다 커야 합니다.')
    now = now or datetime.now(timezone.utc)
    root = Path(directory).expanduser()
    report = {'ready': False, 'max_age_hours': max_age_hours,
              'valid_count': 0, 'invalid': [], 'partial_count': 0,
              'newest_created_at': None, 'age_hours': None, 'errors': []}
    try:
        if not root.is_dir():
            raise OSError('unavailable')
        entries = sorted(root.iterdir())
    except OSError:
        report['errors'].append('백업 저장 경로에 접근할 수 없습니다.')
        return report
    newest = None
    for entry in entries:
        if entry.name.startswith('.sunil-partial-'):
            report['partial_count'] += 1
            continue
        if not entry.name.startswith('sunil-'):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                raise ValidationError('완료 백업 폴더가 아닙니다.')
            _, manifest = verify_backup(entry)
            created = datetime.fromisoformat(manifest.get('created_at', ''))
            if created.tzinfo is None or created.utcoffset() is None:
                raise ValueError('timezone required')
            if created > now:
                raise ValueError('future backup')
            newest = max(newest, created) if newest else created
            report['valid_count'] += 1
        except (ValidationError, OSError, ValueError, TypeError):
            report['invalid'].append(entry.name)
    if newest:
        report['newest_created_at'] = newest.isoformat()
        report['age_hours'] = round((now - newest).total_seconds() / 3600, 2)
        if (now - newest).total_seconds() > max_age_hours * 3600:
            report['errors'].append('최신 정상 백업이 허용 시간을 초과했습니다.')
    else:
        report['errors'].append('검증된 완료 백업이 없습니다.')
    if report['invalid']:
        report['errors'].append('손상되었거나 형식이 잘못된 백업을 확인하세요.')
    if report['partial_count']:
        report['errors'].append('복사 중이거나 중단된 임시 백업이 있습니다. 작업 로그를 확인하세요.')
    report['ready'] = not report['errors']
    return report
