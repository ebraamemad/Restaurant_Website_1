from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Admin, StoreSettings
from app.security import hash_password


def main() -> None:
    with SessionLocal() as db:
        admin = db.scalar(select(Admin).where(Admin.username == settings.admin_username))
        if admin is None:
            db.add(
                Admin(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    full_name="Store Admin",
                )
            )
        store_settings = db.scalar(select(StoreSettings))
        if store_settings is None:
            db.add(StoreSettings(store_name=settings.app_name))
        db.commit()
    print("Admin and store settings are ready.")


if __name__ == "__main__":
    main()
