from pydantic import BaseModel, EmailStr, Field

class RegisterRequest(BaseModel):
    name: str | None = None
    email: EmailStr
    password: str = Field(min_length=6)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str
