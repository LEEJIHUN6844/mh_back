## 설치 해야되는거
```
# 가상환경 설치
python -m venv venv
# 가상환경 실행
source .venv/bin/activate      

# 라이브러리들 설치 명령어
pip install -r requirements.txt
```
## 실행
1. 백엔드 가상환경 실행
2. 백엔드 실행 `uvicorn app.main:app --reload`
3. 프론트 실행 `npm start`
4. `http://localhost:3000/` 실행
5. 개발자 모드에서 `console.log`가 제대로 뜨면 성공
메인에선 **Hi FastAPI!**가 뜨고 다른 페이지 들어가면 다른 로그가 떠야 정상 연결 안 뜨면 메인만 연결된 것이고 그 이후 페이지는 프론트에서만 연결된 페이지 인 것 