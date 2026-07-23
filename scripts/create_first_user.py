import argparse
import os
import sys

from dotenv import load_dotenv

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app
from db import init_db, get_db
from kanban.models import create_user


def main():
    parser = argparse.ArgumentParser(description='Cria o primeiro usuario administrador no banco configurado.')
    parser.add_argument('--name', required=True, help='Nome do usuario')
    parser.add_argument('--key', required=True, help='Chave de login com 8 caracteres')
    parser.add_argument('--password', required=True, help='Senha inicial')
    parser.add_argument('--color', default='#3b82f6', help='Cor do usuario')
    args = parser.parse_args()

    load_dotenv()

    with app.app_context():
        init_db()
        db = get_db()
        existing_user = db.execute('SELECT id FROM users WHERE key = ? LIMIT 1', (args.key,)).fetchone()
        if existing_user:
            raise SystemExit('Ja existe um usuario com essa chave.')

        created = create_user(
            args.name,
            args.password,
            key=args.key,
            color=args.color,
            photo=None,
            is_admin=1
        )
        if not created:
            raise SystemExit('Nao foi possivel criar o usuario administrador.')

        print('Usuario administrador criado com sucesso.')


if __name__ == '__main__':
    main()
