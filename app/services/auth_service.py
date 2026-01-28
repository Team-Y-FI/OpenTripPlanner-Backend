from sqlalchemy.ext.asyncio import AsyncSession
from app.core.exceptions import AppError
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token, decode_token
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.repositories.token_repo import RefreshTokenRepository

class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.users = UserRepository(db)
        self.tokens = RefreshTokenRepository(db)

    async def register(self, name: str | None, email: str, password: str):
        if await self.users.get_by_email(email):
            raise AppError("email_exists", "Email already exists", 409)

        user = User(name=name, email=email, password_hash=hash_password(password))
        user = await self.users.create(user)

        access = create_access_token(user.user_id)
        refresh, exp = create_refresh_token(user.user_id)
        await self.tokens.create(user.user_id, refresh, exp)
        return user, access, refresh

    async def login(self, email: str, password: str):
        user = await self.users.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise AppError("invalid_credentials", "Invalid email or password", 401)

        access = create_access_token(user.user_id)
        refresh, exp = create_refresh_token(user.user_id)
        await self.tokens.create(user.user_id, refresh, exp)
        return user, access, refresh

    async def refresh(self, refresh_token: str):
        if not await self.tokens.is_active(refresh_token):
            raise AppError("token_expired", "Refresh token expired or revoked", 401)
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise AppError("unauthorized", "Invalid refresh token", 401)
        user_id = payload.get("sub")
        if not user_id:
            raise AppError("unauthorized", "Invalid refresh token", 401)
        return create_access_token(user_id)

    async def logout(self, refresh_token: str):
        await self.tokens.revoke(refresh_token)
