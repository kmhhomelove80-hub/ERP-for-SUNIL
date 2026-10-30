"""Small single-worker deployment login throttle; no passwords are logged."""
import hashlib
from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse

class WarehouseLoginView(LoginView):
    template_name='inventory/login.html'
    def throttle_key(self):
        # REMOTE_ADDR is intentionally used; do not trust arbitrary client headers.
        identity=self.request.POST.get('username','').strip().casefold()+'|'+self.request.META.get('REMOTE_ADDR','')
        return 'sunil-login:'+hashlib.sha256(identity.encode()).hexdigest()
    def dispatch(self,request,*args,**kwargs):
        if not settings.DEBUG and request.method=='POST' and cache.get(self.throttle_key(),0)>=5:
            response=HttpResponse('로그인 시도가 많습니다. 10분 후 다시 시도하세요.',status=429,content_type='text/plain; charset=utf-8')
            response['Retry-After']='600'
            return response
        return super().dispatch(request,*args,**kwargs)
    def form_invalid(self,form):
        if not settings.DEBUG:
            key=self.throttle_key();cache.add(key,0,timeout=600)
            try:cache.incr(key)
            except ValueError:cache.set(key,1,timeout=600)
        return super().form_invalid(form)
    def form_valid(self,form):
        cache.delete(self.throttle_key())
        return super().form_valid(form)
