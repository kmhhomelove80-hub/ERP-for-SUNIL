from django.core.management.base import BaseCommand,CommandError
from django.core.exceptions import ValidationError
from inventory.backups import create_backup

class Command(BaseCommand):
    help='DB 일관 백업과 SHA-256 manifest를 별도 디렉터리에 생성합니다.'
    def add_arguments(self,parser):
        parser.add_argument('--directory',required=True)
        parser.add_argument('--pg-bin')
    def handle(self,*args,**options):
        try:target=create_backup(options['directory'],options['pg_bin'])
        except (ValidationError,OSError) as error:raise CommandError(str(error)) from error
        self.stdout.write(str(target))
