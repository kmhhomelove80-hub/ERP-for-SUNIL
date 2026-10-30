from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from openpyxl import Workbook
from .archives import inspect_archive,apply_archive
from .models import Stock,LegacyArchiveRecord,LegacyArchiveBatch

class ArchiveTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('archive',is_staff=True)
    def fixture(self,path):
        w=Workbook();s=w.active;s.title='BACKUP';s['A1']='ITEM No.';s['K1']='ITEM No.'
        for c,v in zip('ABCDEF',['OLD1','모델','사양',3,'A01','2026103001']):s[f'{c}2']=v
        for c,v in zip(['K','L','M','N','O','P','Q'],['OLD1','모델','사양',2,'A01','2026103002','호선']):s[f'{c}2']=v
        log=w.create_sheet('작업로그');log.append(['Timestamp','사용자','종류','상세']);log.append(['2026-10-01T10:00','old-user','Picking','기존 피킹 기록'])
        w.save(path)
    def test_history_import_is_readonly_and_idempotent(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'legacy.xlsx';self.fixture(p)
            plan=inspect_archive(p);self.assertEqual(plan['counts'],{'receipt':1,'picking':1,'log':1})
            batch=apply_archive(plan,self.user);self.assertEqual(apply_archive(plan,self.user).pk,batch.pk)
            self.assertEqual(LegacyArchiveRecord.objects.count(),3);self.assertFalse(Stock.objects.exists())
            self.client.force_login(self.user)
            response=self.client.get('/archives/',{'q':'호선','kind':'picking'})
            self.assertContains(response,'OLD1');self.assertEqual(response.context['page_obj'].paginator.count,1)
    def test_failure_rolls_back_archive(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'legacy.xlsx';self.fixture(p);plan=inspect_archive(p)
            with patch('inventory.archives.audit',side_effect=RuntimeError('audit unavailable')):
                with self.assertRaises(RuntimeError):apply_archive(plan,self.user)
            self.assertFalse(LegacyArchiveBatch.objects.exists());self.assertFalse(LegacyArchiveRecord.objects.exists())
