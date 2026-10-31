"""Real browser workflow on a disposable DB, with synthetic data only.

Run using Python with Playwright installed, Chromium available, and .venv set up.
No real credentials, source Excel data or production database are used.
"""
import argparse
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from openpyxl import load_workbook
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser()
parser.add_argument('--artifacts',default='/tmp/sunil-browser-artifacts')
parser.add_argument('--chromium',default='/usr/bin/chromium')
args=parser.parse_args()
artifacts=Path(args.artifacts).resolve();artifacts.mkdir(parents=True,exist_ok=True)
python=root/'.venv/bin/python'
with tempfile.TemporaryDirectory(prefix='sunil-browser-') as directory, tempfile.TemporaryFile() as log:
    environment=os.environ.copy()
    # Explicitly select disposable SQLite even if a production PG profile exists.
    environment.pop('ERP_DB_NAME',None)
    environment['ERP_SQLITE_PATH']=str(Path(directory)/'db.sqlite3')
    password=secrets.token_urlsafe(24)
    environment['ERP_SMOKE_PASSWORD']=password
    erp_file=Path(directory)/'erp.xlsx'
    environment['ERP_SMOKE_FILE']=str(erp_file)
    subprocess.run([str(python),'manage.py','migrate','--noinput'],cwd=root,env=environment,check=True,stdout=subprocess.DEVNULL)
    seed="""import os
from django.contrib.auth import get_user_model
from inventory.models import Item, Stock
get_user_model().objects.create_user('browser-admin',password=os.environ['ERP_SMOKE_PASSWORD'],is_staff=True,is_superuser=True)
a=Item.objects.create(code='REQUEST-A',name='고객 요청 등')
b=Item.objects.create(code='ACTUAL-B',name='대체 등')
Stock.objects.create(item=a,location='OLD-A',quantity=5)
Stock.objects.create(item=b,location='OLD-B',quantity=5)
"""
    subprocess.run([str(python),'manage.py','shell','-c',seed],cwd=root,env=environment,check=True,stdout=subprocess.DEVNULL)
    environment.pop('ERP_SMOKE_PASSWORD',None)
    erp_direct=Path(directory)/'y09.xls'
    erp_direct.write_bytes('ABC분류\t품목코드\t품목명\t현재고\t출하대기\n\tREQUEST-A\t요청 등\t-5.0\t.0\n\tACTUAL-B\t대체 등\t5.0\t.0\n합계\t\t\t.0\t.0\n'.encode('cp949'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    command=[str(python),'manage.py','runserver',f'127.0.0.1:{port}','--noreload']
    process=subprocess.Popen(command,cwd=root,env=environment,stdout=log,stderr=log)
    try:
        for _ in range(50):
            if process.poll() is not None:
                log.seek(0);raise RuntimeError(log.read().decode())
            try:
                with urlopen(base+'/login/',timeout=1) as response:
                    if response.status==200:break
            except URLError:time.sleep(.1)
        else:raise RuntimeError('server startup timed out')
        with sync_playwright() as p:
            from playwright.sync_api import expect
            browser=p.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            pc=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];pc.on('pageerror',lambda e:errors.append(str(e)))
            pc.goto(base+'/login/')
            pc.get_by_label('사용자 이름',exact=False).fill('browser-admin')
            pc.get_by_label('비밀번호',exact=False).fill(password)
            pc.get_by_role('button',name='로그인',exact=True).click();pc.wait_for_url(base+'/')
            pc.goto(base+'/picking/new/')
            pc.get_by_label('프로젝트 / 호선',exact=False).fill('빌림 상환 검증 호선')
            pc.get_by_label('고객 자료 6열 붙여넣기',exact=False).fill('POR-001\t4283\tB1\t고객 요청 등\t10\tREQUEST-A')
            pc.get_by_role('button',name='배정 미리보기',exact=True).click()
            pc.get_by_label('부족 수량을 확인했으며',exact=False).check()
            pc.get_by_role('button',name='위 배정 결과로 예약 등록',exact=True).click();pc.wait_for_url('**/picking/*/')
            picking=pc.url
            pc.get_by_role('link',name='대체품 배정',exact=True).click()
            pc.get_by_label('고객 요청 코드',exact=False).select_option('REQUEST-A')
            pc.get_by_label('빌릴 제품 / 위치',exact=False).select_option(label='ACTUAL-B | 대체 등 | OLD-B | 가용 5')
            pc.get_by_label('빌릴 수량',exact=False).fill('5')
            pc.get_by_label('대체 사유',exact=False).fill('동일 제품 확인, 필요한 후크 수정')
            pc.get_by_label('요청 코드와 빌릴 코드',exact=False).check()
            pc.get_by_role('button',name='확인 후 반영',exact=True).click();pc.wait_for_url('**/substitutions/*/')
            loan=pc.url
            when=(datetime.now(ZoneInfo('Asia/Seoul'))-timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M')
            pc.get_by_label('실제 대체 출고 시각',exact=False).fill(when)
            pc.get_by_label('동일 제품이거나 필요한 수정을',exact=False).check()
            pc.get_by_role('button',name='대체 출고(빌림)',exact=True).click()
            expect(pc.get_by_text('미상환 5개',exact=False)).to_be_visible()
            pc.screenshot(path=str(artifacts/'borrow-dispatched.png'),full_page=True)
            # Ordinary release screen must contain only requested A, never B.
            pc.goto(base+'/allocations/?q=빌림 상환 검증 호선')
            assert pc.locator('input[name=allocations]').count()==1
            pc.locator('input[name=allocations]').check()
            pc.get_by_label('실제 출고 시각',exact=False).fill(when)
            pc.get_by_label('선택한 행과 수량',exact=False).check()
            pc.get_by_role('button',name='선택 범위 전량 처리',exact=True).click()
            # ERP A -5 / B +5 remains as source; linked loan is visible.
            pc.goto(base+'/erp/upload/')
            pc.get_by_label('자료 이름',exact=False).fill('빌림 출고 후 ERP')
            pc.get_by_label('회사 ERP 자료 기준 시각',exact=False).fill((datetime.now(ZoneInfo('Asia/Seoul'))-timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M'))
            pc.get_by_label('Y09 부산 ERP 추출',exact=False).set_input_files(str(erp_direct))
            pc.get_by_role('button',name='자료 미리보기',exact=True).click()
            pc.get_by_label('자료 기준 시각과 창고별 수량',exact=False).check()
            pc.get_by_role('button',name='이 자료로 대조 등록',exact=True).click();pc.wait_for_url('**/erp/*/')
            snapshot=pc.url
            expect(pc.get_by_role('link',name='1건 · 기준시각 미상환 5개').first).to_be_visible()
            pc.screenshot(path=str(artifacts/'erp-linked-loan.png'),full_page=True)
            # Production receipt of A goes to a NEW physical location.
            pc.goto(base+'/receipts/new/')
            pc.get_by_label('품목',exact=False).select_option(label='REQUEST-A')
            pc.get_by_label('입고 구분',exact=False).select_option('received')
            pc.get_by_label('수량',exact=False).fill('5')
            pc.get_by_label('실제 위치 / 예정 재고 식별자',exact=False).fill('NEW-RECEIPT')
            pc.get_by_role('button',name='확인 후 반영',exact=True).click();pc.wait_for_url(base+'/')
            # Partial then complete repayment, without extra inbound/outbound.
            for quantity in ['3','2']:
                pc.goto(loan)
                pc.get_by_label('상환에 사용할 요청 코드 입고 건',exact=False).select_option(index=1)
                pc.get_by_label('상환 수량',exact=False).fill(quantity)
                pc.get_by_label('상환 근거 / 제품 수정 내용',exact=False).fill('생산 입고품 확인 및 후크 수정 완료')
                pc.get_by_label('입고품이 빌린 제품과 동일하거나',exact=False).check()
                pc.get_by_role('button',name='입고분으로 상환',exact=True).click()
            expect(pc.get_by_text('대체 상환 완료',exact=False)).to_be_visible()
            pc.get_by_label('회사 ERP 정산 확인',exact=False).select_option('confirm')
            pc.get_by_label('확인 / 확인 취소 근거',exact=False).fill('생산 입고 반영과 회사 ERP 코드별 수량 확인')
            pc.get_by_label('회사 ERP 자료를 확인했습니다.',exact=False).check()
            pc.get_by_role('button',name='확인 기록 저장',exact=True).click()
            pc.screenshot(path=str(artifacts/'repaid-and-erp-confirmed.png'),full_page=True)
            # Frozen old snapshot still shows the old 5 outstanding units.
            pc.goto(snapshot)
            expect(pc.get_by_role('link',name='1건 · 기준시각 미상환 5개').first).to_be_visible()
            pc.goto(picking)
            pc.get_by_role('link',name='고객 양식 인쇄',exact=True).click()
            expect(pc.locator('table.customer')).to_contain_text('대체 ACTUAL-B')
            expect(pc.locator('table.customer')).to_contain_text('OLD-B [ 5ea ]')
            expect(pc.locator('.picking-quantity-summary')).to_contain_text('요청 총수량 10개')
            expect(pc.locator('.picking-quantity-summary')).to_contain_text('피킹 배정 총수량 10개')
            pc.pdf(path=str(artifacts/'customer-borrow-print.pdf'),prefer_css_page_size=True,print_background=True)
            printed=subprocess.check_output(['pdftotext',str(artifacts/'customer-borrow-print.pdf'),'-'],text=True).replace(' ','').replace('\n','')
            assert '요청총수량10개' in printed and '피킹배정총수량10개' in printed
            pc.goto(base+'/?q=ACTUAL-B')
            expect(pc.locator('.desktop-stock-table')).to_contain_text('NEW-RECEIPT')
            expect(pc.locator('#stock-results > .card')).to_contain_text('현재고 합계 5')
            pc.screenshot(path=str(artifacts/'repaid-physical-stock.png'),full_page=True)
            assert not errors,errors
            verify='''from inventory.models import Stock,Substitution,SubstitutionSettlement
from django.db.models import Sum
assert Stock.objects.aggregate(q=Sum('quantity'))['q']==5
assert Stock.objects.get(item__code='ACTUAL-B',location='NEW-RECEIPT').quantity==5
assert Stock.objects.get(item__code='ACTUAL-B',location='OLD-B').quantity==0
assert sum(Stock.objects.filter(item__code='REQUEST-A').values_list('quantity',flat=True))==0
s=Substitution.objects.get();assert s.allocation.state=='settled' and s.erp_confirmed_at
assert s.allocation.released_at is None
assert SubstitutionSettlement.objects.count()==2
'''
            subprocess.run([str(python),'manage.py','shell','-c',verify],cwd=root,env=environment,check=True,stdout=subprocess.DEVNULL)
            report=dict(picking_totals_in_screen_and_pdf=True,request_10_normal_5_borrow_5=True,ordinary_release_excludes_substitution=True,temporary_physical_outbound=True,partial_3_then_2_repayment=True,repay_conserves_quantity_and_new_location=True,erp_confirmation_no_inventory_mutation=True,old_snapshot_stays_outstanding_5=True,customer_print_tracks_requested_actual_codes=True,javascript_errors=errors,synthetic_data_only=True)
            (artifacts/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2));browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
