from fastapi import APIRouter, Request, Form, HTTPException, Response, Depends
from passlib.context import CryptContext
import mysql.connector
import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# DB 연결 함수
def get_db():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB")
    )

router = APIRouter()

# 요청 Body 스키마 정의
class SignupRequest(BaseModel):
    UserName: str
    UserID: str
    UserPW: str

class LoginRequest(BaseModel):
    UserID: str
    UserPW: str

# 회원가입
@router.post("/signup")
async def signup(user: SignupRequest):
    if not user.UserName or not user.UserID or not user.UserPW:
        raise HTTPException(status_code=400, detail="모든 필드를 입력하세요.")

    hashed_pw = pwd_context.hash(user.UserPW)
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO user (UserName, UserID, UserPW) VALUES (%s, %s, %s)",
            (user.UserName, user.UserID, hashed_pw)
        )
        conn.commit()
    except mysql.connector.Error as err:
        if err.errno == 1062:  # 중복 키 에러
            raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")
        else:
            raise HTTPException(status_code=500, detail=f"DB 오류: {err}")
    finally:
        cursor.close()
        conn.close()
    return {"message": "회원가입 성공!"}

# 로그인
@router.post("/login")
async def login(user: LoginRequest, response: Response):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM user WHERE UserID = %s", (user.UserID,))
    user_db = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user_db:
        raise HTTPException(status_code=401, detail="존재하지 않는 아이디입니다.")
    if not pwd_context.verify(user.UserPW, user_db["UserPW"]):
        raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다.")

    # 쿠키 발급
    response.set_cookie(key="UserID", value=user_db["UserID"], httponly=True, samesite="none")
    return {
        "message": "로그인 성공!",
        "UserName": user_db["UserName"]
    }

# 로그아웃
@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("userID")
    return {"message": "로그아웃 되었습니다."}
