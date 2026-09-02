import argparse
import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.auth.bootstrap import bootstrap_first_admin
from app.database.session import SessionLocal
from app.errors import APIError


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first administrator")
    parser.add_argument("email")
    args = parser.parse_args()
    try:
        email = str(TypeAdapter(EmailStr).validate_python(args.email))
    except ValidationError as error:
        parser.error(str(error))

    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        parser.error("Passwords do not match")
    if not 12 <= len(password) <= 128:
        parser.error("Password must contain between 12 and 128 characters")

    with SessionLocal() as db:
        try:
            admin = bootstrap_first_admin(db, email=email, password=password)
            db.commit()
        except APIError as error:
            db.rollback()
            parser.error(f"{error.code}: {error.message}")
    print(f"Administrator created: {admin.email}")


if __name__ == "__main__":
    main()
