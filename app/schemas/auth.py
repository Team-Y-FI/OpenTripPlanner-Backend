from pydantic import BaseModel, EmailStr, Field

class SendVerificationRequest(BaseModel):
    email: EmailStr

class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None
    phone_number: str | None = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str