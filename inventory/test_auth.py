from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase,override_settings

@override_settings(DEBUG=False,SECURE_SSL_REDIRECT=False)
class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear();self.addCleanup(cache.clear)
        self.user=get_user_model().objects.create_user('operator',password='valid-test-password')
    def test_repeated_failed_login_is_throttled(self):
        for _ in range(5):self.assertEqual(self.client.post('/login/',{'username':'operator','password':'wrong'}).status_code,200)
        response=self.client.post('/login/',{'username':'operator','password':'wrong'})
        self.assertEqual(response.status_code,429);self.assertEqual(response['Retry-After'],'600')
        self.assertEqual(self.client.get('/login/').status_code,200)
    def test_success_resets_failed_attempts(self):
        self.client.post('/login/',{'username':'operator','password':'wrong'})
        self.assertEqual(self.client.post('/login/',{'username':'operator','password':'valid-test-password'}).status_code,302)
        self.client.post('/logout/')
        for _ in range(5):self.assertEqual(self.client.post('/login/',{'username':'operator','password':'wrong'}).status_code,200)
