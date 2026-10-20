"""Crea una cuenta. Uso: python -m app.create_user NOMBRE_USUARIO [--admin]"""
import getpass
import re
import sys

import psycopg
from pwdlib import PasswordHash

from app.main import DATABASE_URL, ensure_schema

args = [a for a in sys.argv[1:] if a != "--admin"]
make_admin = "--admin" in sys.argv[1:]
if len(args) != 1:
    print("Uso: python -m app.create_user NOMBRE_USUARIO [--admin]")
    sys.exit(1)
username = args[0].strip()
if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", username):
    print("El usuario debe tener entre 3 y 80 caracteres: letras, números, punto, guion o guion bajo.")
    sys.exit(1)
password = getpass.getpass("Nueva contraseña: ")
if password != getpass.getpass("Repite la contraseña: "):
    print("Las contraseñas no coinciden.")
    sys.exit(1)
if len(password) < 12:
    print("Usa una contraseña de al menos 12 caracteres.")
    sys.exit(1)
ensure_schema()
try:
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("INSERT INTO app_users(username,password_hash,role) VALUES(%s,%s,%s)",
                     (username, PasswordHash.recommended().hash(password), "admin" if make_admin else "user"))
    print(f"Usuario '{username}' creado{' como administrador' if make_admin else ''}.")
except psycopg.errors.UniqueViolation:
    print("Ese usuario ya existe. No se modificó su contraseña.")
    sys.exit(1)
