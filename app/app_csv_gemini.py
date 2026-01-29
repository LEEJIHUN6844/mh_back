# app_csv_gemini.py
# CSV → 후보 선별(규칙) → Gemini가 일정 구성(JSON) → 서버가 why/요약 톤을 일괄 정제
# 실행: uvicorn app_csv_gemini:app --host 0.0.0.0 --port 8000 --reload

import os, json, re, time, datetime
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from difflib import SequenceMatcher

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

# ------------------ 환경 ------------------
load_dotenv()
CSV_PATH = os.getenv("PLACES_CSV") or os.path.join(os.path.dirname(__file__), "places_master_json.csv")
MODEL_ID_ENV = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""
if not GEMINI_API_KEY:
    raise SystemExit("GEMINI_API_KEY가 필요합니다 (.env).")

# bullets 처리 파라미터(고정)
BULLET_MAX = 2
BULLET_SIM_THRESHOLD = 0.9

# ------------------ Gemini ------------------
import google.generativeai as genai
from google.api_core import exceptions as gexc
genai.configure(api_key=GEMINI_API_KEY)

# ------------------ CSV 캐시 ------------------
_DF: Optional[pd.DataFrame] = None
_DF_MTIME: Optional[float] = None
_LAST_LOAD_AT: Optional[str] = None

REQUIRED_COLS = [
    "storeid","wide_area","basic_area","storename","category","category_top",
    "rating","address","url","hon0_index_final","summary_bullets_json"
]

# ------------------ 로딩/정제 유틸 ------------------
def _read_csv(path: str) -> pd.DataFrame:
    last = None
    for enc in ("utf-8-sig","utf-8","cp949",None):
        try:
            return pd.read_csv(path, encoding=enc) if enc else pd.read_csv(path)
        except Exception as e:
            last = e
    raise last

def _parse_bullets_json(s: str) -> List[str]:
    if isinstance(s, list): return [str(x).strip() for x in s if str(x).strip()]
    if not isinstance(s, str) or not s.strip(): return []
    try:
        arr = json.loads(s)
        return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        parts = re.split(r"[\r\n]+", s)
        return [p.strip("-• ").strip() for p in parts if p.strip()]

def _uniq_exact(seq: List[str]) -> List[str]:
    seen=set(); out=[]
    for s in seq:
        k=str(s).strip()
        if not k: continue
        kl=k.lower()
        if kl not in seen:
            seen.add(kl); out.append(k)
    return out

def _dedup_similar(lines: List[str], th=BULLET_SIM_THRESHOLD) -> List[str]:
    kept=[]
    for s in lines:
        t=s.strip()
        if not t: continue
        if any(SequenceMatcher(None,t.lower(),x.lower()).ratio()>=th for x in kept): 
            continue
        kept.append(t)
    return kept

_NEGATIVE_HINTS = [
    "불편","시끄럽","아쉽","비싸","부족","최악","별로","실망","불친절","못하","못함","안 좋","문제","불만"
]

def _is_negative(s: str) -> bool:
    low = s.lower()
    return any(h in low for h in _NEGATIVE_HINTS)

def _clean_bullets(lines: List[str], max_n=BULLET_MAX) -> List[str]:
    # 1) 노이즈/완전중복 제거 → 2) 유사중복 제거 → 3) 부족시 톱업(완화) → 4) 부정 제거
    uniq=_uniq_exact([ln for ln in (lines or []) if isinstance(ln,str) and len(ln.strip())>=4])
    dedup=_dedup_similar(uniq, th=BULLET_SIM_THRESHOLD)
    out=dedup[:]
    if len(out)<max_n:
        relaxed=0.8
        for s in uniq:
            if s in out: continue
            if all(SequenceMatcher(None,s.lower(),t.lower()).ratio()<relaxed for t in out):
                out.append(s)
            if len(out)>=max_n: break
    out = [x for x in out if not _is_negative(x)]
    return out[:max_n]

def _infer_kind(storeid: str) -> str:
    if not isinstance(storeid,str) or not storeid: return "기타"
    return {"1":"혼밥","2":"혼숙","3":"혼놀"}.get(storeid[0],"기타")

def _ensure_df_loaded() -> Tuple[pd.DataFrame,float]:
    global _DF,_DF_MTIME,_LAST_LOAD_AT
    try:
        mtime=os.path.getmtime(CSV_PATH)
    except FileNotFoundError:
        raise SystemExit(f"CSV 파일 없음: {CSV_PATH}")
    if _DF is None or _DF_MTIME!=mtime:
        df=_read_csv(CSV_PATH)
        miss=[c for c in REQUIRED_COLS if c not in df.columns]
        if miss: raise SystemExit(f"CSV 컬럼 누락: {miss}")
        for c in ["storeid","wide_area","basic_area","storename","category","category_top","address","url"]:
            df[c]=df[c].astype(str).str.strip()
        for c in ["rating","hon0_index_final"]:
            df[c]=pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        df["kind"]=df["storeid"].apply(_infer_kind)
        df["summary_bullets_list"]=df["summary_bullets_json"].apply(_parse_bullets_json).apply(_clean_bullets)
        _DF, _DF_MTIME = df, mtime
        _LAST_LOAD_AT = datetime.datetime.now().isoformat()
    return _DF,_DF_MTIME

# ------------------ 문장 합성(일관 톤) ------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+","",str(s).lower())

def _rating_phrase(r: float) -> str:
    if r >= 4.5: return "평점이 매우 높습니다"
    if r >= 4.2: return "평점이 높은 편입니다"
    if r >= 4.0: return "평가가 좋은 편입니다"
    return ""

def _cat_phrase(ct: str, cg: str) -> str:
    ct, cg = (ct or "").strip(), (cg or "").strip()
    if ct and cg and ct != cg: return f"{ct}({cg})에 특화되어 있습니다"
    if ct: return f"{ct}에 특화되어 있습니다"
    if cg: return f"{cg}에 특화되어 있습니다"
    return "특징이 뚜렷합니다"

def _shorten(s: str, n=40) -> str:
    s=s.strip()
    return s if len(s)<=n else s[:n].rstrip()+"..."

def _summary_paragraph(place: dict) -> str:
    """
    selections[*].summary_bullets 로 쓸 한 문단(객관/긍정/존댓말)
    예) "이 장소는 양식(피자)에 특화되어 있습니다. 도우 식감이 쫀득하고 재료가 신선하다는 평가가 있습니다. 평점이 높은 편입니다."
    """
    ct, cg = place.get("category_top",""), place.get("category","")
    rating = float(place.get("rating") or 0.0)
    bullets = [b for b in (place.get("summary_bullets_list") or []) if b.strip()]

    parts = []
    parts.append(f"이 장소는 { _cat_phrase(ct, cg) }.")
    if bullets:
        feat = " · ".join(_shorten(x) for x in bullets[:2])
        parts.append(f"{feat} 등의 특징이 있습니다.")
    rp = _rating_phrase(rating)
    if rp:
        parts.append(f"{rp}.")
    txt = " ".join(parts)
    # 마침표 정리
    txt = re.sub(r"\s+\.", ".", txt).strip()
    return txt

def _compose_polite_one_liner(place: dict, kind: str, kw_for_kind: List[str]) -> str:
    """
    일정 why 문장: '이 장소는 …합니다. …분께 추천드립니다.'
    """
    ct = place.get("category_top") or place.get("category") or ""
    rating = float(place.get("rating") or 0.0)

    parts = [_cat_phrase(place.get("category_top",""), place.get("category",""))]
    bullets = [b for b in (place.get("summary_bullets_list") or []) if b.strip()]
    if bullets:
        parts.append(" · ".join(_shorten(x) for x in bullets[:2]))
    rp = _rating_phrase(rating)
    if rp: parts.append(rp)

    feature_clause = " · ".join(p for p in parts if p).rstrip(".")
    target = (" · ".join([k for k in (kw_for_kind or []) if k.strip()][:2]) + " 취향을 선호하시는") if kw_for_kind else f"{kind}을(를) 즐기시는"
    return f"이 장소는 {feature_clause}. {target} 분께 추천드립니다."

# ------------------ 비즈니스 로직 ------------------
def _area_filter(df: pd.DataFrame, location: str) -> pd.DataFrame:
    loc=str(location).strip()
    if not loc: return df
    m = df["wide_area"].str.contains(loc, na=False) | df["basic_area"].str.contains(loc, na=False)
    for t in [t for t in re.split(r"\s+", loc) if t]:
        m = m | df["wide_area"].str.contains(t, na=False) | df["basic_area"].str.contains(t, na=False)
    return df[m]

def _rank_and_pick(df: pd.DataFrame, kind: str, keywords: List[str], per_kind_limit: int) -> List[Dict]:
    # 점수: 완전일치 +2 / 부분일치 +1 / bullets 포함 +0.5 → hon0/rating 정렬
    kw_norm={_norm(k) for k in (keywords or []) if str(k).strip() and len(_norm(k))>=2}
    def match_score(row):
        ct=_norm(row["category_top"]); cg=_norm(row["category"]); score=0.0
        if ct in kw_norm or cg in kw_norm: score+=2.0
        else:
            for kw in kw_norm:
                if kw in ct or ct in kw or kw in cg or cg in kw:
                    score+=1.0; break
        bullets_text=_norm(" ".join(row.get("summary_bullets_list") or []))
        if any(kw in bullets_text for kw in kw_norm): score+=0.5
        return score
    ranked = sorted(df.to_dict("records"),
                    key=lambda r: (match_score(r), r["hon0_index_final"], r["rating"]),
                    reverse=True)[:per_kind_limit]

    out=[]
    for r in ranked:
        # selections에는 한 문단 요약(문자열)로 제공
        summary_paragraph = _summary_paragraph({
            "category_top": r["category_top"],
            "category": r["category"],
            "rating": r["rating"],
            "summary_bullets_list": r.get("summary_bullets_list") or []
        })
        out.append({
            "storeid": r["storeid"],
            "storename": r["storename"],
            "category_top": r["category_top"],
            "category": r["category"],
            "rating": float(r["rating"]),
            "hon0_index_final": float(r["hon0_index_final"]),
            "address": r["address"],
            "url": r["url"],
            "summary_bullets": summary_paragraph,  # ← 한 문단 문자열
            # why 생성용으로 내부에서도 원본 bullets 접근하도록 전달(폴리시: 서버 내부에서만 활용)
            "_summary_bullets_list": r.get("summary_bullets_list") or [],
        })
    return out

def _parse_keywords_map(s: Dict[str, List[str]]|None) -> Dict[str,List[str]]:
    if not s: return {}
    out={}
    for k,v in s.items():
        toks=[]; seen=set()
        for x in (v or []):
            x=str(x).strip()
            if not x: continue
            xn=_norm(x)
            if len(xn)>=2 and xn not in seen:
                seen.add(xn); toks.append(x)
        out[k]=toks
    return out

# ------------------ 프롬프트 ------------------
def _system_instruction() -> str:
    return (
        "당신은 여행 플래너입니다.\n"
        "입력은 지역/기간과 selections(후보 목록)입니다.\n"
        "규칙:\n"
        "1) selections 안에서만 고르십시오. 외부 장소를 추가하지 마십시오.\n"
        "2) 각 kind(혼밥/혼숙/혼놀)별로 per_kind_limit 개수를 모두 itinerary에 배치하십시오.\n"
        "3) 각 블록의 why는 **한 문장 문자열**로 작성하십시오(배열 금지). 다음 스타일을 반드시 지키십시오.\n"
        "   - 존댓말, 긍정 톤, 객관적 서술(1인칭/체험·감탄 금지)\n"
        "   - 줄임표(… 또는 ...) 사용 금지, 나열기호(·, -, •) 사용 금지\n"
        "   - 불필요한 괄호/인용부호 금지(카테고리 표기는 예외적으로 '양식(피자)'처럼 허용)\n"
        "   - 핵심 특징 1가지만 자연스럽게 녹여 쓰고, 마지막에 '…분께 추천드립니다.'로 마무리\n"
        "   - 예시: '이 장소는 양식(피자)에 특화되어 있으며 재료가 신선하다는 평가가 있습니다. 혼밥을 즐기시는 분께 추천드립니다.'\n"
        "4) 항상 순수 JSON만 반환하십시오."
    )

def _output_schema_note() -> str:
    return (
        '예시: {"summary":{"location":"...","nights":0,"days":1},'
        '"itinerary":[{"day":1,"blocks":[{"time_hint":"오전","kind":"혼놀","storeid":"...","title":"...","why":"이 장소는 …합니다. …분께 추천드립니다.","map":"..."}]}]}'
    )

def _build_gemini_payload(req: dict, selections: Dict[str, List[Dict]]) -> Dict:
    # selections는 summary_bullets(문자열)만 포함 → 모델이 그대로 쓰거나 참고
    return {
        "locale":"ko-KR",
        "location":req["location"],
        "nights":req["nights"],
        "days":req["nights"]+1,
        "per_kind_limit":req["per_kind_limit"],
        "kinds":req["kinds"],
        "priority_keywords":req.get("keywords_map",{}),
        "selections":{k:[{kk:vv for kk,vv in it.items() if kk!="_summary_bullets_list"} for it in arr]
                      for k,arr in selections.items()}
    }

def _call_gemini(payload: dict) -> dict:
    def make_model(mid:str):
        return genai.GenerativeModel(
            model_name=mid,
            generation_config={
                "temperature":0.2,
                "response_mime_type":"application/json",
                "max_output_tokens":1024
            }
        )
    def ask(mid:str):
        model=make_model(mid)
        inp=[_system_instruction(),"아래 JSON은 후보 데이터입니다.",_output_schema_note(),json.dumps(payload,ensure_ascii=False)]
        resp=model.generate_content(inp)
        txt=(resp.text or "").strip()
        try:
            return json.loads(txt)
        except Exception:
            m=re.search(r"\{.*\}\s*$", txt, re.S)
            if not m: raise RuntimeError("Gemini 응답 파싱 실패: "+txt[:400])
            return json.loads(m.group(0))
    order=[MODEL_ID_ENV] + ([] if MODEL_ID_ENV=="gemini-1.5-flash" else ["gemini-1.5-flash"])
    last=None
    for mid in order:
        for _ in range(2):
            try:
                return ask(mid)
            except gexc.ResourceExhausted as e:
                last=e; delay=getattr(getattr(e,"retry_delay",None),"seconds",None) or 2
                time.sleep(min(delay,10))
            except Exception as e:
                last=e; break
    if isinstance(last,gexc.ResourceExhausted): raise last
    raise RuntimeError(str(last))

# ------------------ 안전장치 & 최종 톤 통일 ------------------
def _fallback_why(kind:str, item:dict, kw_map:Dict[str,List[str]]) -> str:
    # selections에서 내부용 bullets 리스트를 복구해 한 문장 생성
    place = {
        "category_top": item.get("category_top",""),
        "category": item.get("category",""),
        "rating": item.get("rating",0.0),
        "summary_bullets_list": item.get("_summary_bullets_list") or []
    }
    return _compose_polite_one_liner(place, kind, kw_map.get(kind, []))

def _overwrite_all_why(plan: dict, selections: Dict[str,List[dict]], kw_map: Dict[str,List[str]]) -> dict:
    """모든 블록 why를 서버에서 일관 템플릿으로 재작성(항상)."""
    sel_map={it["storeid"]:(k,it) for k,arr in selections.items() for it in arr}
    for day in plan.get("itinerary",[]):
        for b in day.get("blocks",[]):
            sid=str((b or {}).get("storeid","")); kind=(b or {}).get("kind","")
            if sid in sel_map:
                k,it=sel_map[sid]
                why=_fallback_why(k, it, kw_map)
            else:
                # 모델이 외부를 넣을 일은 없게 했지만, 방어적으로 처리
                why=_compose_polite_one_liner({"category_top":b.get("category_top",""),
                                               "category":b.get("category",""),
                                               "rating":0.0,"summary_bullets_list":[]},
                                              kind or "혼놀", kw_map.get(kind, []))
            b["why"]=why
    return plan

def _enforce_counts(plan: dict, selections: Dict[str,List[dict]], per_kind_limit: int, kw_map: Dict[str,List[str]]) -> dict:
    def cnt(p):
        d=defaultdict(int)
        for day in p.get("itinerary",[]):
            for b in day.get("blocks",[]): d[(b or {}).get("kind","")] += 1
        return d
    def used(p):
        u=set()
        for day in p.get("itinerary",[]):
            for b in day.get("blocks",[]): 
                sid=(b or {}).get("storeid"); 
                if sid: u.add(sid)
        return u
    plan.setdefault("alternatives",{})
    used_ids=used(plan); counts=cnt(plan)
    for kind, items in selections.items():
        need=max(0, per_kind_limit - counts.get(kind,0))
        if need==0: continue
        plan["alternatives"].setdefault(kind,[])
        for it in items:
            if need==0: break
            if it["storeid"] in used_ids: continue
            plan["alternatives"][kind].append({
                "storeid":it["storeid"],"title":it["storename"],
                "why": _fallback_why(kind, it, kw_map),
                "map": it["url"]
            })
            need-=1
    return plan

# ------------------ API ------------------
class RecommendRequest(BaseModel):
    location:str
    nights:int
    kinds:List[str]                 # ["혼밥","혼숙","혼놀"]
    keywords:Optional[Dict[str,List[str]]]=None  # {"혼밥":["양식"], "혼놀":["공원"]}

class PlaceOut(BaseModel):
    storeid:str; storename:str; category_top:str; category:str
    rating:float; hon0_index_final:float; address:str; url:str
    summary_bullets:str             # ← 한 문단 문자열로 변경

class PlanResponse(BaseModel):
    meta:Dict[str,object]
    selections:Dict[str,List[PlaceOut]]
    plan:Dict[str,object]

app=FastAPI(title="Solo Planner (CSV + Gemini)")

@app.get("/health")
def health():
    df,mt=_ensure_df_loaded()
    return {"status":"ok","rows":len(df),"csv":CSV_PATH,"csv_mtime":mt,"last_loaded_at":_LAST_LOAD_AT,"model":MODEL_ID_ENV}

@app.post("/plan", response_model=PlanResponse)
def plan(req:RecommendRequest):
    if req.nights<0: raise HTTPException(400,"nights는 0 이상")
    per_kind_limit=2*(req.nights+1)
    df,_=_ensure_df_loaded()

    df_loc=_area_filter(df, req.location)
    if df_loc.empty: raise HTTPException(404,"지역과 일치하는 데이터가 없습니다.")

    kw_map=_parse_keywords_map(req.keywords)
    selections={}
    for kind in req.kinds:
        sub=df_loc[df_loc["kind"]==kind]
        selections[kind]=_rank_and_pick(sub, kind, kw_map.get(kind, []), per_kind_limit)

    if all(len(v)==0 for v in selections.values()):
        raise HTTPException(404,"조건에 맞는 후보가 없습니다.")

    payload=_build_gemini_payload(
        {"location":req.location,"nights":req.nights,"kinds":req.kinds,
         "keywords_map":kw_map,"per_kind_limit":per_kind_limit},
        selections
    )
    try:
        plan_json=_call_gemini(payload)
    except gexc.ResourceExhausted as e:
        detail={"message":"Gemini API quota exceeded",
                "hint":"모델을 flash로 두고, 입력 텍스트를 줄여보세요."}
        ra=getattr(getattr(e,"retry_delay",None),"seconds",None)
        if ra: detail["retry_after_seconds"]=ra
        raise HTTPException(429, detail)
    except Exception as e:
        raise HTTPException(500, str(e))

    # 누락 보충 + 모든 why를 일관 템플릿으로 강제
    if isinstance(plan_json, dict):
        plan_json=_enforce_counts(plan_json, selections, per_kind_limit, kw_map)
        plan_json=_overwrite_all_why(plan_json, selections, kw_map)

    return {
        "meta":{"location":req.location,"nights":req.nights,"per_kind_limit":per_kind_limit,
                "model":MODEL_ID_ENV,"timestamp":datetime.datetime.now().isoformat()},
        "selections":selections,
        "plan":plan_json
    }
