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

# 혼밥 가게 데이터
@router.get("/eating")
def get_eating_data():
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT storeid, searchid, wide_area, basic_area, keyword, storename, category, rating, review_cnt, address, url, img_url, honbab_cnt FROM store_eating"
        )
        results = cursor.fetchall()
        cursor.close()
        db.close()
        return {"data": results}
    except Exception as e:
        return {"error": str(e)}

# 혼밥 리뷰 데이터
@router.get("/eating/{storeId}/review")
def get_eating_review(storeId: str):
    try:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT reviewid, storeid, searchid, reviewidx, reviewtxt FROM review_eating WHERE storeId=%s ORDER BY reviewId",
            (storeId,)
        )
        reviews = cursor.fetchall()
        cursor.close()
        db.close()
        return {"reviews": reviews}
    except Exception as e:
        return {"error": str(e)}
