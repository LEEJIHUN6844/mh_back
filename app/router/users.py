from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from datetime import datetime
import mysql.connector
import os
from dotenv import load_dotenv

load_dotenv()
router = APIRouter()

def get_db():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB")
    )

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

# 좋아요 삭제
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

# 마이페이지
@router.get("/mypage")
async def get_mypage(request: Request):
    user_id = request.cookies.get("userID")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    # 유저 이름
    cursor.execute("SELECT UserName FROM user WHERE UserID = %s", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="유저를 찾을 수 없습니다.")

    # 좋아요 목록
    cursor.execute(
        "SELECT store_id, keyword, item_name, address, category, image FROM likes WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,)
    )
    likes = cursor.fetchall()
    cursor.close()
    conn.close()

    return {"UserName": user["UserName"], "likes": likes}

# 로드맵 모음 페이지 (확정 X)
@router.get("/{userID}/roadmap")
def get_user_roadmap(userID: int):
    return {"message": f"User {userID} roadmap"}

