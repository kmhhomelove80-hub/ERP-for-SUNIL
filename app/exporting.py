from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
import io
def safe_value(v):
    # 문자열이 다운로드 파일에서 수식으로 해석되는 것 방지
    return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v

def styled_sheet(w,name,headers,rows):
    s=w.create_sheet(name);s.append(headers)
    for row in rows:s.append([safe_value(v) for v in row])
    s.freeze_panes='A2';s.auto_filter.ref=s.dimensions
    for c in s[1]:c.font=Font(name='맑은 고딕',bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='173D42')
    from openpyxl.utils import get_column_letter
    for j,h in enumerate(headers,1):s.column_dimensions[get_column_letter(j)].width=48 if h=='품명' else 18
    for row in s.iter_rows(min_row=2):
        for c in row:c.alignment=Alignment(vertical='center');c.font=Font(name='맑은 고딕',size=10)
    return s


def export_result(result,erp):
    w=Workbook();w.remove(w.active)
    s=styled_sheet(w,'추출',['조선소',*result['headers']],[[r.get('yard',''),*r['values']] for r in result['rows']])
    for i,rec in enumerate(result['rows'],2):
        if rec['unmatched']:
            for c in s[i]:c.fill=PatternFill('solid',fgColor='FFEBEB')
        for col in [6,8,14,15,16]:s.cell(i,col).number_format='#,##0'
    start=2;records=result['rows']
    for idx,rec in enumerate(records):
        end=idx+2
        if idx==len(records)-1 or (rec.get('yard'),str(rec['values'][5]).casefold())!=(records[idx+1].get('yard'),str(records[idx+1]['values'][5]).casefold()):
            if end>start:s.merge_cells(start_row=start,start_column=8,end_row=end,end_column=8)
            start=end+1
    styled_sheet(w,'처리안내',['항목','내용'],[['행 통합 기준','POR·호선·블록·품명·품목코드 동일, 수량만 합산'],['금액','첫 번째 일치 행 유지']]+[['안내',v] for v in result['warnings']])
    out=io.BytesIO();w.save(out);return out.getvalue()
