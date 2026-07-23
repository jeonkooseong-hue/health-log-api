"""인증(로그인) 관련 도구: 비밀번호 해시, JWT 토큰, 현재 사용자 확인"""
import os
import secrets

import bcrypt
import jwt
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from database import get_db, User

# 토큰 서명용 비밀키. 배포 시 반드시 환경변수 SECRET_KEY 를 지정한다.
# 지정하지 않으면 실행할 때마다 임의 키가 생성되어 기존 토큰은 모두 무효가 된다.
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", "12"))

# /docs의 Authorize 버튼과 연결. 토큰 발급 창구는 /login
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def hash_password(password: str) -> str:
    """평문 비밀번호 → 해시(단방향 암호화). 원문 복구 불가."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """입력 비번이 저장된 해시와 맞는지 확인."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_access_token(username: str) -> str:
    """사용자 이름을 담은 JWT 토큰(출입증) 발급."""
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """토큰을 검사해 지금 로그인한 사용자를 돌려준다. 실패하면 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증 실패: 토큰이 없거나 잘못됨",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None or user.status != "active":
        raise credentials_exception
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """관리자(admin) 또는 슈퍼관리자(superadmin)만 통과. 대시보드 조회용."""
    if current_user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한이 필요합니다")
    return current_user


def get_current_superadmin(current_user: User = Depends(get_current_user)) -> User:
    """슈퍼관리자(superadmin)만 통과. 권한 변경 등 관리 행위용."""
    if current_user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="슈퍼관리자 권한이 필요합니다")
    return current_user
