from pathlib import Path
import uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from inventory.importing import inspect_workbook, apply_import
from inventory.archives import inspect_archive, apply_archive
from inventory.reconciliation import inspect_erp_file, create_snapshot
from inventory.models import Stock, Allocation, LegacyArchiveRecord, ImportBatch, ErpSnapshot


class Command(BaseCommand):
    help = '원본 전체 자료를 별도 노트북 DB에 이관합니다. 원본은 수정하지 않습니다.'

    def add_arguments(self, parser):
        parser.add_argument('--source')
        parser.add_argument('--phone')

    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE != 'config.laptop_real_settings':
            raise CommandError('원본 자료 전용 노트북 설정에서만 실행합니다.')
        source, phone = options['source'], options['phone']
        if not source or not phone:
            try:
                import tkinter as tk
                from tkinter.filedialog import askopenfilename
                root = tk.Tk(); root.withdraw()
                try:
                    source = source or askopenfilename(title='1. Original SUNIL ERP .xlsm', filetypes=[('Excel macro workbook', '*.xlsm')])
                    if source:
                        phone = phone or askopenfilename(title='2. SEARCH_PHONE.xlsx', filetypes=[('Excel workbook', '*.xlsx')])
                finally:
                    root.destroy()
            except Exception as error:
                raise CommandError('파일 선택 창을 열 수 없습니다. --source 및 --phone 경로를 지정하세요.') from error
        if not source or not phone:
            raise CommandError('파일 선택을 취소했습니다. 데이터 변경 없음.')
        actor = get_user_model().objects.filter(is_active=True, is_superuser=True).order_by('pk').first()
        if not actor:
            raise CommandError('먼저 이 시험 DB의 관리자 계정을 만드세요.')
        self.stdout.write('Reading original files. This can take 1-3 minutes...')
        self.stdout.flush()
        try:
            plan = inspect_workbook(Path(source), ['11/3입고(류문학)', '9/10 입고'])
            archives = [inspect_archive(Path(p)) for p in [source, phone]]
            with Path(source).open('rb') as stream:
                erp = inspect_erp_file(stream, 'legacy')
            with transaction.atomic():
                if not ImportBatch.objects.filter(digest=plan['digest']).exists():
                    apply_import(plan, actor)
                for archive in archives:
                    apply_archive(archive, actor)
                snapshot_id = uuid.uuid5(uuid.NAMESPACE_URL, 'sunil-laptop-original:'+erp['digest'])
                if not ErpSnapshot.objects.filter(pk=snapshot_id).exists():
                    create_snapshot(request_id=snapshot_id,
                                    label='업로드 원본 ERP 대조 / 원본 기준시각 미확인',
                                    erp_as_of=timezone.now(), plan=erp, actor=actor)
            self.stdout.write(str({'stock_rows': Stock.objects.count(),
                                  **Stock.objects.aggregate(quantity=Sum('quantity'), reserved=Sum('reserved')),
                                  'active_picking_rows': Allocation.objects.filter(state='reserved').count(),
                                  'archive_rows': LegacyArchiveRecord.objects.count()}))
            self.stdout.write('Original files were not changed. Full-data trial is ready.')
            for warning in plan['warnings']:
                self.stdout.write(str(warning))
        except (ValidationError, OSError, ValueError, KeyError) as error:
            raise CommandError(str(error)) from error
