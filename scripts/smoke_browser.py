"""Real browser workflow on a disposable DB, with synthetic data only.

Run using Python with Playwright installed, Chromium available, and .venv set up.
No real credentials, source Excel data or production database are used.
"""
import argparse
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
admin=get_user_model().objects.create_user('browser-admin',password=os.environ['ERP_SMOKE_PASSWORD'],is_staff=True)
get_user_model().objects.create_user('browser-mobile',password=os.environ['ERP_SMOKE_PASSWORD'])
item=Item.objects.create(code='DEMO-LAMP-001',name='검증용 선박등기구',specification='브라우저 검증용 가상 품목')
stock=Stock.objects.create(item=item,location='A0101',quantity=20)
reserve(stock_id=stock.pk,project='검증용 호선',quantity=4,actor=admin)
from openpyxl import Workbook
w=Workbook();s=w.active;s.title='ERP재고';s.append(['창고','ERP 코드','현재고','출하대기','모델명']);s.append(['Y09','DEMO-LAMP-001',18,12,'검증등']);s.append(['Y02','DEMO-LAMP-001',30,4,'검증등']);w.save(os.environ['ERP_SMOKE_FILE'])
'''
    subprocess.run([str(python),'manage.py','shell','-c',seed],cwd=root,env=environment,check=True,stdout=subprocess.DEVNULL)
    environment.pop('ERP_SMOKE_PASSWORD',None)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    process=subprocess.Popen([str(python),'manage.py','runserver',f'127.0.0.1:{port}','--noreload'],cwd=root,env=environment,stdout=log,stderr=log)
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
            browser=p.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            mobile=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            desktop=browser.new_context(viewport={'width':1440,'height':1000})
            phone,pc=mobile.new_page(),desktop.new_page()
            errors=[]
            for page in [phone,pc]:page.on('pageerror',lambda error:errors.append(str(error)))
            def login(page,username):
                page.goto(base+'/login/')
                page.get_by_label('사용자 이름',exact=False).fill(username)
                page.get_by_label('비밀번호',exact=False).fill(password)
                page.get_by_role('button',name='로그인',exact=True).click()
                page.wait_for_url(base+'/')
            login(phone,'browser-mobile')
            assert phone.locator('.mobile-stock-cards').get_by_text('DEMO-LAMP-001',exact=False).count()==1
            phone.screenshot(path=str(artifacts/'mobile-stock.png'),full_page=True)
            phone.get_by_role('link',name='이동 요청',exact=True).click()
            phone.get_by_label('이동할 위치',exact=False).fill('B0202')
            phone.get_by_label('요청 수량',exact=False).fill('5')
            phone.get_by_label('품목·출발 위치·도착 위치·수량을 확인했습니다.',exact=False).check()
            phone.get_by_role('button',name='이동 요청 등록',exact=True).click()
            phone.wait_for_url('**/movements/*/')
            detail=phone.url
            assert phone.get_by_text('관리자 처리 대기 중입니다.',exact=False).is_visible()
            assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
            phone.screenshot(path=str(artifacts/'mobile-request.png'),full_page=True)
            login(pc,'browser-admin')
            pc.goto(detail)
            pc.screenshot(path=str(artifacts/'desktop-process.png'),full_page=True)
            pc.get_by_label('대상 요청과 수량을 확인했습니다.',exact=False).check()
            pc.get_by_role('button',name='확인 후 처리',exact=True).click()
            assert pc.get_by_text('완료',exact=True).is_visible()
            phone.reload()
            assert phone.get_by_text('완료',exact=True).is_visible()
            phone.screenshot(path=str(artifacts/'mobile-completed.png'),full_page=True)
            pc.goto(base+'/history/?action=movement&q=DEMO-LAMP-001')
            assert pc.get_by_text('A0101 → B0202 · 완료',exact=False).is_visible()
            pc.screenshot(path=str(artifacts/'desktop-history.png'),full_page=True)
            pc.goto(base+'/picking/new/')
            pc.get_by_label('프로젝트 / 호선',exact=False).fill('검증용 PDF 호선')
            pc.get_by_label('ERP 코드 · 수량 붙여넣기',exact=False).fill('DEMO-LAMP-001\t1\nDEMO-LAMP-001\t2')
            pc.get_by_role('button',name='배정 미리보기',exact=True).click()
            pc.get_by_role('button',name='위 배정 결과로 예약 등록',exact=True).click()
            pc.wait_for_url('**/picking/*/')
            pc.screenshot(path=str(artifacts/'desktop-picking.png'),full_page=True)
            pc.pdf(path=str(artifacts/'picking-list.pdf'),prefer_css_page_size=True,print_background=True)
            assert (artifacts/'picking-list.pdf').stat().st_size>1000
            pc.goto(base+'/erp/upload/')
            pc.get_by_label('자료 이름',exact=False).fill('검증용 ERP 자료')
            pc.get_by_label('회사 ERP 자료 기준 시각',exact=False).fill('2026-10-01T10:00')
            pc.get_by_label('ERP 재고 파일',exact=False).set_input_files(str(erp_file))
            pc.get_by_role('button',name='자료 미리보기',exact=True).click()
            pc.get_by_label('자료 기준 시각과 창고별 수량을 확인했습니다.',exact=False).check()
            pc.get_by_role('button',name='이 자료로 대조 등록',exact=True).click()
            pc.get_by_role('link',name='미검토',exact=True).click()
            pc.get_by_label('확인 상태',exact=False).select_option('explained')
            pc.get_by_label('차이 원인',exact=False).fill('기준 시각 차이 확인')
            pc.get_by_label('조치 내용',exact=False).fill('ERP 담당자와 확인 예정')
            pc.get_by_role('button',name='확인 후 반영',exact=True).click()
            assert pc.get_by_text('기준 시각 차이 확인',exact=False).is_visible()
            pc.screenshot(path=str(artifacts/'desktop-reconciliation.png'),full_page=True)
            pc.goto(base+'/picking/new/')
            pc.get_by_label('프로젝트 / 호선',exact=False).fill('Y02 참고 검증')
            pc.get_by_label('ERP 코드 · 수량 붙여넣기',exact=False).fill('DEMO-LAMP-001\t99')
            pc.get_by_role('button',name='배정 미리보기',exact=True).click()
            assert '30' in pc.locator('tr.shortage').text_content()
            pc.screenshot(path=str(artifacts/'desktop-shortage.png'),full_page=True)
            pc.goto(base+'/receipts/new/')
            pc.get_by_label('품목',exact=False).select_option(label='DEMO-LAMP-001')
            pc.get_by_label('입고 구분',exact=False).select_option('received')
            pc.get_by_label('수량',exact=False).fill('2')
            pc.get_by_label('실제 위치 / 예정 재고 식별자',exact=False).fill('D0404')
            pc.get_by_role('button',name='확인 후 반영',exact=True).click()
            pc.goto(base+'/receipts/')
            pc.get_by_role('link',name='원거래 취소',exact=True).click()
            pc.get_by_label('입고 취소 사유',exact=False).fill('브라우저 검증 원거래 취소')
            pc.get_by_label('원거래와 전량 취소 수량을 확인했습니다.',exact=False).check()
            pc.get_by_role('button',name='확인 후 반영',exact=True).click()
            assert pc.get_by_text('취소됨',exact=False).is_visible()
            assert not errors,errors
            report={'mobile_viewport':'390x844','desktop_viewport':'1440x1000','mobile_submit_admin_process_mobile_result':True,'no_mobile_detail_page_horizontal_overflow':True,'history_search':True,'picking_paste_confirm_and_pdf':True,'javascript_errors':errors,'synthetic_data_only':True,'erp_upload_compare_review':True,'y02_shortage_reference':True,'receipt_create_and_cancel':True}
            (artifacts/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False,indent=2))
            browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
