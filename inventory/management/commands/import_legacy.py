import json
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from inventory.importing import inspect_workbook, apply_import

class Command(BaseCommand):
    help = 'Excel 초기 재고/활성 예약을 검증합니다. --apply 없이 DB를 변경하지 않습니다.'
    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--expected-location', action='append', default=[])
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--actor')
    def handle(self, *args, **options):
        try:
            plan = inspect_workbook(options['path'], options['expected_location'])
            self.stdout.write(json.dumps({'digest':plan['digest'],'summary':plan['summary'],'warnings':plan['warnings']},ensure_ascii=False,indent=2))
            if options['apply']:
                if not options['actor']: raise CommandError('--apply에는 --actor 사용자명이 필요합니다.')
                actor = get_user_model().objects.get(username=options['actor'],is_active=True,is_staff=True)
                apply_import(plan,actor)
                self.stdout.write(self.style.SUCCESS('초기 이관 완료'))
            else: self.stdout.write('검증만 완료: DB 변경 없음')
        except (ValidationError, OSError, KeyError, ValueError, get_user_model().DoesNotExist) as error:
            raise CommandError(str(error)) from error
