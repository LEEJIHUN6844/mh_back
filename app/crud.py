#DB 작업 함수

from sqlalchemy.orm import Session
from . import models, schemas
import hashlib

def create_user(db: Session, user: schemas.UserCreate):
    hashed_pw = hashlib.sha256(user.password.encode()).hexdigest()
    db_user = models.User(email=user.email, password=hashed_pw)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user
