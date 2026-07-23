import os
import sys
from dotenv import load_dotenv

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from db import init_db


def main():
    load_dotenv()
    init_db()
    print('Schema inicializado com sucesso.')


if __name__ == '__main__':
    main()
