import json
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand,CommandError
from inventory.archives import inspect_archive,apply_archive

class Command(BaseCommand):
    help='원본의 과거 이력을 읽기 전용 보관으로 이관합니다. 재고에는 반영하지 않습니다.'
    def add_arguments(self,parser):
        parser.add_argument('path');parser.add_argument('--apply',action='store_true');parser.add_argument('--actor')
    def handle(self,*args,**options):
        try:
            plan=inspect_archive(options['path'])
            self.stdout.write(json.dumps(plan['counts'],ensure_ascii=False))
            if options['apply']:
                if not options['actor']:raise CommandError('--actor 관리자 사용자명이 필요합니다.')
                actor=get_user_model().objects.get(username=options['actor'],is_active=True,is_staff=True)
                apply_archive(plan,actor)
                self.stdout.write('이력 보관 이관 완료. 재고 변경 없음.')
            else:self.stdout.write('검증만 완료. DB 변경 없음.')
        except (OSError,ValidationError,get_user_model().DoesNotExist) as error:raise CommandError(str(error)) from error
