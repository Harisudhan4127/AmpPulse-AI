from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    name: str | None = Field(default=None, max_length=64)


class LoginResponse(BaseModel):
    success: bool = True
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    role: str

    class Config:
        from_attributes = True


LoginResponse.model_rebuild()
