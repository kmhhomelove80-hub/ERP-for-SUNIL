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
    seed='''import os
from django.contrib.auth import get_user_model
from inventory.models import Item, Stock
from inventory.services import reserve
admin=get_user_model().objects.create_user('browser-admin',password=os.environ['ERP_SMOKE_PASSWORD'],is_staff=True,is_superuser=True)
get_user_model().objects.create_user('browser-mobile',password=os.environ['ERP_SMOKE_PASSWORD'])
item=Item.objects.create(code='DEMO-LAMP-001',name='검증용 선박등기구',specification='브라우저 검증용 가상 품목')
stock=Stock.objects.create(item=item,location='A0101',quantity=20)
reserve(stock_id=stock.pk,project='검증용 호선',quantity=4,actor=admin)
extra=Item.objects.create(code='CUSTOMER-LAMP',name='고객 조명')
Stock.objects.create(item=extra,location='B0105',quantity=7)
Stock.objects.create(item=extra,location='D0404',quantity=5)
range_item=Item.objects.create(code='RANGE-CODE',name='범위 선택 시험')
range_stock=Stock.objects.create(item=range_item,location='R1',quantity=100)
for i in range(52):
 a=reserve(stock_id=range_stock.pk,project='RANGE-PROJECT',quantity=1,actor=admin)
 a.legacy_seq='SEQ-RANGE';a.save()
from openpyxl import Workbook
w=Workbook();s=w.active;s.title='ERP재고';s.append(['창고','ERP 코드','현재고','출하대기','모델명']);s.append(['Y09','DEMO-LAMP-001',18,12,'검증등']);s.append(['Y02','DEMO-LAMP-001',30,4,'검증등']);w.save(os.environ['ERP_SMOKE_FILE'])
'''
    subprocess.run([str(python),'manage.py','shell','-c',seed],cwd=root,env=environment,check=True,stdout=subprocess.DEVNULL)
    environment.pop('ERP_SMOKE_PASSWORD',None)
    from openpyxl import Workbook
    receipt_file=Path(directory)/'receipts.xlsx'
    book=Workbook();sheet=book.active
    sheet.append(['품목코드','품목명','사양','수량','Location']);sheet.append(['UPLOAD-NEW','업로드 품목','규격',4,'U01']);book.save(receipt_file)
    for warehouse in ['Y09','Y02']:
        text='ABC분류\t품목코드\t품목명\t현재고\t출하대기\n\tCUSTOMER-LAMP\t고객 조명\t12.0\t.0\n합계\t\t\t12.0\t.0\n'
        (Path(directory)/(warehouse+'.xls')).write_bytes(text.encode('cp949'))
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
            pc.get_by_role('button',name='로그인',exact=True).click()
            pc.wait_for_url(base+'/')
            search=pc.get_by_role('textbox',name='재고 검색')
            search.fill('CUSTOMER-LAMP')
            expect(pc.locator('#stock-results > .card')).to_contain_text('2개 항목')
            expect(search).to_be_focused()
            search.dispatch_event('compositionstart')
            search.fill('고객 조명')
            search.dispatch_event('compositionend')
            expect(pc.locator('#stock-results > .card')).to_contain_text('2개 항목')
            search.fill('nothing-will-match')
            expect(pc.locator('#stock-results')).to_contain_text('조회할 재고가 없습니다')
            search.fill('RANGE-CODE');search.fill('CUSTOMER-LAMP')
            expect(pc.locator('#stock-results > .card')).to_contain_text('2개 항목')
            assert 'CUSTOMER-LAMP' in pc.locator('#stock-export').get_attribute('href')
            pc.screenshot(path=str(artifacts/'live-stock.png'),full_page=True)
            # Paste and file routes both have a preview with no writes.
            pc.goto(base+'/receipts/bulk/')
            pc.get_by_label('A~E열 붙여넣기',exact=False).fill('BULK-NEW\t일괄 품목\t규격\t3\tN01\nBULK-NEW\t일괄 품목\t규격\t2\tN02')
            pc.get_by_role('button',name='입고 미리보기').click()
            pc.get_by_label('행별 수량·Location',exact=False).check()
            pc.get_by_role('button',name='전체 행 일괄 입고').click()
            pc.wait_for_url('**/receipts/')
            pc.goto(base+'/receipts/bulk/')
            pc.get_by_label('또는 입고 Excel 파일',exact=False).set_input_files(str(receipt_file))
            pc.get_by_role('button',name='입고 미리보기').click()
            pc.get_by_label('행별 수량·Location',exact=False).check()
            pc.get_by_role('button',name='전체 행 일괄 입고').click()
            pc.wait_for_url('**/receipts/')
            # Customer input reserves once and renders both print formats.
            pc.goto(base+'/picking/new/')
            pc.get_by_label('프로젝트 / 호선',exact=False).fill('고객양식 시험')
            pc.get_by_label('고객 자료 6열 붙여넣기',exact=False).fill('POR-1\t4282\tT51C\t고객 조명\t4\tCUSTOMER-LAMP\nPOR-1\t4282\tT62C\t고객 조명\t4\tCUSTOMER-LAMP\nPOR-1\t4282\tT61C\t고객 조명\t4\tCUSTOMER-LAMP')
            pc.get_by_role('button',name='배정 미리보기').click()
            pc.get_by_role('button',name='위 배정 결과로 예약 등록').click()
            pc.wait_for_url('**/picking/*/')
            standard=pc.url
            pc.pdf(path=str(artifacts/'standard-picking.pdf'),prefer_css_page_size=True,print_background=True)
            pc.get_by_role('link',name='고객 양식 인쇄',exact=True).click()
            expect(pc.locator('table.customer')).to_contain_text('B0105 [ 7ea ]')
            expect(pc.locator('table.customer')).to_contain_text('D0404 [ 5ea ]')
            assert pc.locator('td[rowspan="3"]').count()==2
            pc.screenshot(path=str(artifacts/'customer-picking.png'),full_page=True)
            pc.pdf(path=str(artifacts/'customer-picking.pdf'),prefer_css_page_size=True,print_background=True)
            # Shift-range selects/checks and unchecks without processing yet.
            pc.goto(base+'/allocations/?q=RANGE-PROJECT&seq=SEQ-RANGE')
            boxes=pc.locator('input[name=allocations]')
            assert boxes.count()==50
            boxes.nth(0).click();boxes.nth(9).click(modifiers=['Shift'])
            assert pc.locator('input[name=allocations]:checked').count()==10
            boxes.nth(4).click(modifiers=['Shift'])
            assert pc.locator('input[name=allocations]:checked').count()==4
            pc.get_by_role('checkbox',name='현재 페이지 전체 선택',exact=True).check()
            assert pc.locator('input[name=allocations]:checked').count()==50
            pc.get_by_label('필터 결과 전체',exact=False).check()
            pc.get_by_label('실제 출고 시각',exact=False).fill((datetime.now(ZoneInfo('Asia/Seoul'))-timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M'))
            pc.get_by_label('선택한 행과 수량',exact=False).check()
            pc.screenshot(path=str(artifacts/'filtered-release.png'),full_page=True)
            pc.get_by_role('button',name='선택 범위 전량 처리',exact=True).click()
            expect(pc.get_by_text('52개 예약 행을 처리했습니다.',exact=True)).to_be_visible()
            expect(pc.get_by_text('해당하는 진행 중 예약이 없습니다.',exact=True)).to_be_visible()
            # ERP CP949 files directly from the file inputs.
            pc.goto(base+'/erp/upload/')
            pc.get_by_label('자료 이름',exact=False).fill('직접 ERP 추출 시험')
            pc.get_by_label('회사 ERP 자료 기준 시각',exact=False).fill('2026-10-01T10:00')
            pc.get_by_label('파일 형식',exact=False).select_option('export')
            pc.get_by_label('Y09 부산 ERP 추출',exact=False).set_input_files(str(Path(directory)/'Y09.xls'))
            pc.get_by_label('Y02 영주 ERP 추출',exact=False).set_input_files(str(Path(directory)/'Y02.xls'))
            pc.get_by_role('button',name='자료 미리보기',exact=True).click()
            pc.get_by_label('자료 기준 시각과 창고별 수량',exact=False).check()
            pc.get_by_role('button',name='이 자료로 대조 등록').click()
            pc.wait_for_url('**/erp/*/')
            pc.screenshot(path=str(artifacts/'erp-direct-upload.png'),full_page=True)
            assert not errors,errors
            report=dict(live_search=True,paste_and_excel_bulk_receipt=True,customer_print_split_locations=True,shift_range_check_uncheck=True,filtered_release_all_52_rows=True,direct_cp949_y09_y02_upload=True,javascript_errors=errors,synthetic_data_only=True)
            (artifacts/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False,indent=2));browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
