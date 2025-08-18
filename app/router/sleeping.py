from fastapi import APIRouter
import mysql.connector
import os

router = APIRouter()

def get_db():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB")
    )
# 혼숙 가게 데이터
@router.get("/api/sleeping")
def get_sleeping_data():
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT storeId, searchId, wide_area, basic_area, keyword, name, category, rating, review_cnt, address, url, main_photo, hon0_index FROM store_sleeping"
        )
        results = cursor.fetchall()
        cursor.close()
        db.close()
        return {"data": results}
    except Exception as e:
        return {"error": str(e)}

# 혼숙 리뷰 데이터
@router.get("/api/sleeping/{storeId}/review")
def get_sleeping_review(storeId: str):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT reviewId, storeId, searchId, reviewIdx, reviewTxt FROM review_sleeping WHERE storeId=%s ORDER BY reviewId",
            (storeId,)
        )
        reviews = cursor.fetchall()
        cursor.close()
        db.close()
        return {"reviews": reviews}
    except Exception as e:
        return {"error": str(e)}
