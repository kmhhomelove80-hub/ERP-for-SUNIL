"""새 야드의 열 연결과 ERP 매칭 규칙. 설정은 원본과 함께 저장."""
from .engine import text, cell, number, day
from pydantic import BaseModel, Field
import re

class YardConfig(BaseModel):
    name:str=Field(min_length=1,max_length=30,pattern=r'^[A-Za-z0-9가-힣_-]+$')
    header_row:int=Field(default=1,ge=1,le=20)
    match_cols:list[int]=Field(min_length=1,max_length=8)
    erp_match_cols:list[int]=Field(default_factory=lambda:[9],min_length=1,max_length=8)
    remove_hyphens:bool=False
    remove_spaces:bool=False
    hull:int|None=None
    block:int|None=None
    title:int|None=None
    qty:int
    destination:int|None=None
    due:int|None=None
    def checked(self):
        source=[*self.match_cols,self.qty,*[v for v in [self.hull,self.block,self.title,self.destination,self.due] if v is not None]]
        if any(c<1 or c>500 for c in source) or any(c<1 or c>17 for c in self.erp_match_cols):raise ValueError('원본 열은 1~500, ERP 열은 A~Q 범위에서 선택하세요.')
        if self.name in ('ERP','REF','SET','중소','HMD','HHI','HSHI','추출'):raise ValueError('기존 고정 시트명은 신규 야드 이름으로 사용할 수 없습니다.')
        return self.model_dump()

def normalize_key(values,cfg):
    parts=[]
    for value in values:
        part=text(value)
        if cfg.get('remove_hyphens'):part=part.replace('-','')
        if cfg.get('remove_spaces'):part=re.sub(r'\s+','',part)
        parts.append(part)
    return ''.join(parts).casefold()

def configured_source(book,erp,name,cfg):
    rows=book.get(name,[]);start=cfg['header_row']
    if len(rows)<start:raise ValueError(f'{name} 원본 머리글 위치를 확인하세요.')
    emap={};duplicates=0
    for e in erp:
        key=normalize_key([cell(e,c) for c in cfg['erp_match_cols']],cfg)
        if key:
            if key in emap:duplicates+=1
            else:emap[key]=e
    smap={}
    for r in book.get('SET',[])[1:]:
        if text(cell(r,3)):smap.setdefault(text(cell(r,3)),cell(r,6))
    out=[];missing=0
    def mapped(r,field,fallback=None):return cell(r,cfg[field]) if cfg.get(field) else fallback
    for r in rows[start:]:
        parts=[cell(r,c) for c in cfg['match_cols']]
        if not any(text(p) for p in parts):continue
        key=normalize_key(parts,cfg);e=emap.get(key)
        if e is None:missing+=1
        por=text(e[8]) if e else ''.join(text(v) for v in parts)
        out.append([por,text(mapped(r,'hull',e[2] if e else None)),text(mapped(r,'block')),text(mapped(r,'title',e[17] if e else None)),number(mapped(r,'qty')),e[9] if e else None,None,smap.get(por),e[10] if e else None,e[11] if e else None,text(mapped(r,'destination')),day(mapped(r,'due',e[16] if e else None))])
    notes=[]
    if missing:notes.append(f'ERP 매칭 실패 {missing}행 / 전체 {len(out)}행')
    if duplicates:notes.append(f'ERP 중복 매칭키 추가 행 {duplicates}개: 첫 번째 일치 행 사용')
    return out,notes,{'total':len(out),'matched':len(out)-missing,'unmatched':missing,'duplicate_erp':duplicates}
