import os
import random
import pandas as pd
import json
import re
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from datetime import datetime
import mysql.connector
from dotenv import load_dotenv
from pathlib import Path

# ---------------- Gemini ----------------
import google.generativeai as genai

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

router = APIRouter()

# ---------------- DB 연결 ----------------
def get_db():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB")
    )

# ---------------- CSV 불러오기 ----------------
CSV_PATH = Path(__file__).parent / "places_master_json.csv"
df_places = pd.read_csv(CSV_PATH)

# --------------------- 좋아요 관련 ---------------------
class LikeRequest(BaseModel):
    store_id: str
    keyword: str
    item_name: str
    address: str
    category: str
    image: str = None

@router.post("/likes")
async def add_like(request: Request, like: LikeRequest):
    user_id = request.cookies.get("userID")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO likes (store_id, user_id, keyword, item_name, address, category, image, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (like.store_id, user_id, like.keyword, like.item_name, like.address, like.category, like.image, datetime.now())
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"message": "좋아요 저장 성공"}  

@router.delete("/likes")
async def remove_like(request: Request, item_name: str):
    user_id = request.cookies.get("userID")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM likes WHERE user_id=%s AND item_name=%s",
        (user_id, item_name)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"message": f"{item_name} 좋아요 삭제 완료"}

# --------------------- 마이페이지 ---------------------
@router.get("/mypage")
async def get_mypage(request: Request):
    user_id = request.cookies.get("userID")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT UserName FROM user WHERE UserID = %s", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")
    cursor.execute(
        "SELECT store_id, keyword, item_name, address, category, image FROM likes WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,)
    )
    likes = cursor.fetchall()
    cursor.close()
    conn.close()
    return {"UserName": user["UserName"], "likes": likes}

# --------------------- 로드맵 요청 모델 ---------------------
class PlanRequest(BaseModel):
    location: str
    nights: int
    kinds: list[str]             # ["혼밥", "혼놀", "혼숙"]
    keywords: dict[str, list[str]] = {}

# --------------------- CSV + Gemini AI 기반 로드맵 ---------------------
@router.post("/plan")
async def get_plan(req: PlanRequest):
    roadmap = []

    kind_to_category_top = {
        "혼밥": ["한식","분식","양식","패스트푸드"],
        "혼놀": ["디저트","카페","도보","공원","박물관"],
        "혼숙": ["호텔","모텔","펜션","캠핑, 야영"]
    }

    # ---------------- CSV 기반 선택 -----------------
    for day in range(1, req.nights + 2):
        day_plan = []
        for kind in req.kinds:
            categories = kind_to_category_top.get(kind, [])
            df_kind = df_places[df_places['category_top'].isin(categories)] if categories else df_places

            # 지역 필터
            if req.location != "전체 지역":
                loc = req.location.lower()
                mask = (
                    df_kind['wide_area'].str.lower().str.contains(loc, na=False) |
                    df_kind['basic_area'].str.lower().str.contains(loc, na=False) |
                    df_kind['address'].str.lower().str.contains(loc, na=False)
                )
                df_filtered = df_kind[mask]
            else:
                df_filtered = df_kind

            # 키워드 필터
            if kind in req.keywords and req.keywords[kind]:
                kw_mask = df_filtered['summary_bullets'].apply(
                    lambda s: any(kw in str(s) for kw in req.keywords[kind])
                )
                df_filtered = df_filtered[kw_mask]

            if not df_filtered.empty:
                chosen = df_filtered.sample(1).iloc[0].to_dict()
                day_plan.append({
                    "storeid": chosen.get("storeid", ""),
                    "storename": chosen.get("storename", ""),
                    "address": chosen.get("address", ""),
                    "category": kind,
                    "category_top": chosen.get("category_top", ""),
                    "rating": chosen.get("rating", None),
                    "url": chosen.get("url", ""),
                    "hon0_index_final": chosen.get("hon0_index_final", None),
                    "summary_bullets": chosen.get("summary_bullets", "")
                })
        roadmap.append({"day": day, "plan": day_plan})

    # ---------------- Gemini AI 요약 (JSON 안전 출력용) -----------------
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    # 로드맵을 간략화해서 AI 혼동 방지
    roadmap_for_prompt = [
        {"day": d["day"], "plan": [{"storename": p["storename"], "category": p["category"]} for p in d["plan"]]}
        for d in roadmap
    ]
    
    prompt = f"""
    아래는 사용자가 선택한 지역과 추천된 로드맵 데이터입니다.
    지역: {req.location}
    여행 일수: {req.nights + 1}일
    로드맵: {roadmap_for_prompt}

    각 날짜별 "day"와 "summary"를 포함한 JSON 배열만 출력해주세요.
    JSON 외 다른 텍스트는 절대 포함하지 마세요.
    예시:
    [
      {{"day": 1, "summary": "첫날은 카페와 공원을 둘러봅니다."}},
      {{"day": 2, "summary": "둘째날은 호텔 체크인 후 박물관 관람"}}
    ]
    반드시 JSON 형식으로만 출력해주세요.
    """

    response = model.generate_content(prompt)

    day_summaries = []
    if response and response.text:
        print("[DEBUG] AI 응답:", response.text)
        try:
            # JSON 파싱 시도
            day_summaries = json.loads(response.text)
        except json.JSONDecodeError:
            print("[ERROR] JSON 디코딩 실패:", response.text)
            # 정규식으로 day, summary 추출
            matches = re.findall(r'\{.*?"day"\s*:\s*(\d+).*?"summary"\s*:\s*"(.*?)".*?\}', response.text, re.DOTALL)
            day_summaries = [{"day": int(day), "summary": summary.strip()} for day, summary in matches]
            # 최후 fallback
            if not day_summaries:
                day_summaries = [{"day": d["day"], "summary": "AI 설명 생성 실패"} for d in roadmap]

    # ---------------- 요약을 roadmap에 병합 -----------------
    for day_plan in roadmap:
        match = next((s for s in day_summaries if s["day"] == day_plan["day"]), None)
        day_plan["ai_summary"] = match["summary"] if match else "AI 설명 생성 실패"

    return {"region": req.location, "days": req.nights + 1, "roadmap": roadmap}
