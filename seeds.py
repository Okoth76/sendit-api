# seeds.py
from sqlmodel import Session, select
from database.session import engine, create_db_and_tables

# Import BOTH models so SQLModel registers their relationships
from models.user import User
from models.document import Document
from auth import hash_password

def seed_database():
    create_db_and_tables()
    with Session(engine) as session:
        existing_user = session.exec(select(User)).first()
        if existing_user:
            return

        users = [
            User(
                username="admin",
                email="admin@sendit.com",
                hashed_password=hash_password("AdminPass123!"),
                full_name="System Administrator",
                role="admin"
            ),
            User(
                username="manager",
                email="manager@sendit.com",
                hashed_password=hash_password("ManagerPass123!"),
                full_name="Operations Manager",
                role="manager"
            ),
            User(
                username="staff",
                email="staff@sendit.com",
                hashed_password=hash_password("StaffPass123!"),
                full_name="Field Staff",
                role="staff"
            )
        ]
        session.add_all(users)
        session.commit()

if __name__ == "__main__":
    seed_database()