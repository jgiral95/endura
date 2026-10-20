"""Convierte una cuenta existente en administradora. Uso: python -m app.promote_admin NOMBRE_USUARIO"""
import os
import sys

import psycopg


def main():
    if len(sys.argv) != 2:
        print("Uso: python -m app.promote_admin NOMBRE_USUARIO", file=sys.stderr)
        raise SystemExit(2)
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        row = c.execute("UPDATE app_users SET role='admin' WHERE username=%s RETURNING username,is_active",
                        (sys.argv[1].strip(),)).fetchone()
    if not row:
        print(f"No existe el usuario {sys.argv[1]}.", file=sys.stderr)
        raise SystemExit(1)
    print(f"{row[0]} ahora es administrador (activo={row[1]}).")


if __name__ == "__main__":
    main()
