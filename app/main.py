# 진입점 – FastAPI 라우팅
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from . import models, schemas, crud
from .database import engine, SessionLocal
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from app.router import eating, login, playing, sleeping, users

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

# CORS 설정 (리액트에서 요청 가능하도록)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # 프론트엔드 주소(도메인 생기면 그거로 변경)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 기본 라우트
@app.get("/")
def read_root():
    return {"message": "Hi FastAPI!"}

app.include_router(eating.router)
app.include_router(login.router)
app.include_router(playing.router)
app.include_router(sleeping.router)
app.include_router(users.router)


# 세션 미들웨어
import os
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET"),
    max_age=60 * 30
)





if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
