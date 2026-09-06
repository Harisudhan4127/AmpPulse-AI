from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token
from app.core.errors import ApiError
from app.models.models import User
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest, UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=LoginResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new user account and return a signed-in session token."""
    email = payload.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise ApiError("EMAIL_TAKEN", "An account with this email already exists.", 409)

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.name.strip() if payload.name else None,
        role="home",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=user.email, role=user.role)
    return LoginResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise ApiError("AUTH_FAILED", "Invalid email or password.", 401)

    token = create_access_token(subject=user.email, role=user.role)
    return LoginResponse(access_token=token, user=UserOut.model_validate(user))
