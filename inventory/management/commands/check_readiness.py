import json
from django.core.management.base import BaseCommand,CommandError
from inventory.readiness import readiness_report

class Command(BaseCommand):
    help='DB/마이그레이션/예약 합계와 운영 설정을 읽기 전용 점검합니다.'
    def add_arguments(self,parser):parser.add_argument('--deployment',action='store_true')
    def handle(self,*args,**options):
        report=readiness_report(options['deployment'])
        self.stdout.write(json.dumps(report,ensure_ascii=False,indent=2))
        if not report['ready']:raise CommandError('점검 실패. 데이터는 수정하지 않았습니다.')
