from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings


class LaptopImportGuardTests(SimpleTestCase):
    @override_settings(SETTINGS_MODULE='config.settings')
    def test_original_file_import_refuses_normal_settings_before_db_access(self):
        with self.assertRaisesMessage(CommandError, '전용 노트북 설정'):
            call_command('import_laptop_files', source='not-opened.xlsm', phone='not-opened.xlsx')
