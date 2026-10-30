from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from inventory.models import Item, Stock


class Command(BaseCommand):
    help = '노트북 시험 DB가 비어 있을 때만 가상 품목을 등록합니다.'

    @transaction.atomic
    def handle(self, *args, **options):
        expected = settings.BASE_DIR / 'laptop-data' / 'trial.sqlite3'
        if settings.SETTINGS_MODULE != 'config.laptop_settings' or str(settings.DATABASES['default']['NAME']) != str(expected):
            raise CommandError('노트북 전용 시험 설정에서만 실행할 수 있습니다.')
        if Item.objects.exists() or Stock.objects.exists():
            self.stdout.write('기존 데이터가 있어 가상 품목 추가를 생략합니다.')
            return
        for code, name, location, quantity, status in [
            ('DEMO-001', '테스트 조명 A', 'A0101', 20, Stock.Status.RECEIVED),
            ('DEMO-001', '테스트 조명 A', 'B0101', 10, Stock.Status.RECEIVED),
            ('DEMO-002', '테스트 조명 B', '시험 입고 예정', 10, Stock.Status.EXPECTED),
            ('DEMO-003', '테스트 부품 C', 'C0101', 5, Stock.Status.RECEIVED),
        ]:
            item, _ = Item.objects.get_or_create(code=code, defaults={'name': name, 'specification': '실제 회사 품목이 아닌 가상 데이터'})
            Stock.objects.create(item=item, location=location, quantity=quantity, status=status,
                                 expected_date=timezone.localdate()+timedelta(days=1) if status==Stock.Status.EXPECTED else None,
                                 sender='시험 담당자' if status==Stock.Status.EXPECTED else '')
        self.stdout.write('가상 품목 3개 / 위치별 4행 / 총수량 45개를 등록했습니다.')
