# app/schemas/auth.py
from pydantic import BaseModel, EmailStr, Field


class SendVerificationRequest(BaseModel):
    email: EmailStr


class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class RegisterRequest(BaseModel):
    # ✅ 사용자 식별자 직접 입력 저장
    user_id: str = Field(min_length=1, max_length=64)

    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
