from django.core.management.base import BaseCommand,CommandError
from django.core.exceptions import ValidationError
from inventory.backups import verify_backup,restore_backup

class Command(BaseCommand):
    help='체크섬 확인 후 새 파일/빈 PostgreSQL DB에만 복원합니다. 기본은 검증만 수행합니다.'
    def add_arguments(self,parser):
        parser.add_argument('backup_directory')
        parser.add_argument('--sqlite-target')
        parser.add_argument('--pg-bin')
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        try:
            if options['apply']:
                result=restore_backup(options['backup_directory'],sqlite_target=options['sqlite_target'],pg_bin=options['pg_bin'])
            else:
                verify_backup(options['backup_directory']);result='체크섬 검증 완료. --apply 없이 복원하지 않습니다.'
        except (ValidationError,OSError) as error:raise CommandError(str(error)) from error
        self.stdout.write(result)
