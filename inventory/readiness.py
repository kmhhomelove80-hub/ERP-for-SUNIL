"""Read-only operational checks; never repair stock or change configuration."""
from django.conf import settings
from django.db import connection,DatabaseError
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Sum
from django.utils import timezone
from .models import Stock,Allocation,Substitution
from .locking import warehouse_lock

def readiness_report(deployment=False):
    checks=[]
    def add(key,status,label,detail):checks.append(dict(key=key,status=status,label=label,detail=detail))
    try:
        with connection.cursor() as cursor:cursor.execute('SELECT 1');cursor.fetchone()
        add('database','ok','DB 연결','연결 확인')
        executor=MigrationExecutor(connection)
        pending=executor.migration_plan(executor.loader.graph.leaf_nodes())
        add('migrations','error' if pending else 'ok','DB 구조',f'미적용 {len(pending)}개' if pending else '미적용 변경 없음')
        if pending:return {'checks':checks,'ready':False,'warnings':0}
        with warehouse_lock():
            totals={row['stock_id']:row['total'] for row in Allocation.objects.filter(state='reserved').values('stock_id').annotate(total=Sum('quantity'))}
            mismatch=sum(s.reserved!=totals.get(s.pk,0) for s in Stock.objects.all())
            add('reservations','error' if mismatch else 'ok','예약 수량 대조',f'불일치 {mismatch}개' if mismatch else '원장 allocated와 활성 예약 합계 일치')
            from .substitutions import repaid_quantity
            invalid=0
            for record in Substitution.objects.select_related('allocation').prefetch_related('settlements'):
                repaid=repaid_quantity(record);a=record.allocation
                valid=(0<=repaid<=a.quantity)
                if a.state in ['reserved','cancelled']:valid=valid and not record.dispatched_at and repaid==0
                elif a.state=='substituted':valid=valid and bool(record.dispatched_at) and repaid<a.quantity and not record.settled_at
                elif a.state=='settled':valid=valid and bool(record.dispatched_at) and repaid==a.quantity and bool(record.settled_at)
                else:valid=False
                if not valid:invalid+=1
            invalid+=Allocation.objects.filter(state__in=['substituted','settled'],substitution__isnull=True).count()
            add('substitutions','error' if invalid else 'ok','빌림·상환 대조',f'불일치 {invalid}건' if invalid else '대체 출고 상태와 상환 합계 일치')
            expected=Stock.objects.filter(status='expected',quantity__gt=0)
            unknown=expected.filter(expected_date__isnull=True).count()
            late=expected.filter(expected_date__lt=timezone.localdate()).count()
            add('expected_dates','warning' if unknown or late else 'ok','예정 입고 확인',f'예정일 미분류 {unknown}개 / 예정일 경과 {late}개')
            add('data','ok','관리 항목',f'재고 {Stock.objects.count()}개 / 활성 예약 {Allocation.objects.filter(state="reserved").count()}행')
    except DatabaseError:
        add('database','error','DB 확인','DB 연결 또는 구조를 확인하세요. 연결 정보/암호는 표시하지 않습니다.')
    if deployment:
        add('postgresql','ok' if connection.vendor=='postgresql' else 'error','운영 DB','PostgreSQL 사용' if connection.vendor=='postgresql' else '운영 전 PostgreSQL 설정 필요')
        add('debug','error' if settings.DEBUG else 'ok','운영 모드','개발 모드 켜짐' if settings.DEBUG else '개발 모드 꺼짐')
        secure=bool(getattr(settings,'SESSION_COOKIE_SECURE',False) and getattr(settings,'CSRF_COOKIE_SECURE',False) and getattr(settings,'SECURE_SSL_REDIRECT',False))
        add('https_settings','ok' if secure else 'error','HTTPS 설정','설정 확인; 실제 인증서·접속 검증은 별도' if secure else '운영 HTTPS/cookie 설정 필요')
    return dict(checks=checks,ready=not any(c['status']=='error' for c in checks),warnings=sum(c['status']=='warning' for c in checks))
