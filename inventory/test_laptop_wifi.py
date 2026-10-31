import importlib.util
import os
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase

spec=importlib.util.spec_from_file_location('laptop_launcher',Path(__file__).resolve().parent.parent/'scripts'/'laptop.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class LaptopWifiTests(SimpleTestCase):
    def test_only_rfc1918_ipv4_is_selected(self):
        for value in ['192.168.1.20','10.0.0.1','172.16.3.5']:
            self.assertTrue(launcher.private_ipv4(value))
        for value in ['127.0.0.1','0.0.0.0','8.8.8.8','169.254.1.2','::1','invalid']:
            self.assertFalse(launcher.private_ipv4(value))
        with self.assertRaises(ValueError):launcher.choose_wifi_ip('0.0.0.0')

    def test_auto_ip_is_deduplicated_and_public_addresses_excluded(self):
        rows=[(None,None,None,None,('192.168.1.2',0)),(None,None,None,None,('192.168.1.2',0)),(None,None,None,None,('8.8.8.8',0))]
        with patch.object(launcher.socket,'getaddrinfo',return_value=rows):
            self.assertEqual(launcher.choose_wifi_ip(),'192.168.1.2')

    def test_wifi_profile_is_explicit_and_normal_run_clears_inherited_wifi(self):
        with patch.dict(os.environ,{'ERP_DB_NAME':'production-must-not-use','SUNIL_LAPTOP_WIFI_IP':'192.168.9.9'}),patch.object(launcher.subprocess,'run') as runner:
            launcher.run(['manage.py','check'])
            env=runner.call_args.kwargs['env']
            self.assertNotIn('ERP_DB_NAME',env);self.assertNotIn('SUNIL_LAPTOP_WIFI_IP',env)
            with patch.object(launcher,'WIFI_IP','192.168.1.2'),patch.object(launcher,'REAL',True):
                launcher.run(['manage.py','check'])
            env=runner.call_args.kwargs['env']
            self.assertEqual(env['DJANGO_SETTINGS_MODULE'],'config.laptop_wifi_settings')
            self.assertEqual(env['SUNIL_LAPTOP_WIFI_REAL'],'1')
