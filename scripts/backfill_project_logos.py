import os
import sys

from dotenv import load_dotenv
from werkzeug.datastructures import FileStorage

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app
from storage import save_upload, StorageClientError, is_r2_enabled
from projects.models import load_projects, update_project_logo

DEFAULT_LOGO_PATH = os.path.join(ROOT_DIR, 'static', 'img', 'logoqpilot.png')


def main():
    load_dotenv()

    if not is_r2_enabled():
        print('R2 nao esta configurado neste ambiente (variaveis R2_* ausentes). Nada foi enviado.')
        return

    if not os.path.exists(DEFAULT_LOGO_PATH):
        print(f'Arquivo padrao nao encontrado em {DEFAULT_LOGO_PATH}.')
        return

    with app.app_context():
        projects = [p for p in load_projects() if not p.get('logo_path')]
        if not projects:
            print('Nenhum projeto sem logo para atualizar.')
            return

        updated = 0
        for project in projects:
            with open(DEFAULT_LOGO_PATH, 'rb') as fh:
                file_storage = FileStorage(stream=fh, filename='logoqpilot.png', content_type='image/png')
                try:
                    stored_path = save_upload(
                        file_storage,
                        object_key=f'logos/{project["id"]}.png',
                        local_relative_path=f'uploads/logos/{project["id"]}.png'
                    )
                except StorageClientError as exc:
                    print(f'Falha ao enviar logo do projeto {project["id"]}: {exc}')
                    continue
            update_project_logo(project['id'], stored_path)
            updated += 1

        print(f'{updated} de {len(projects)} projeto(s) atualizados com o icone padrao no R2.')


if __name__ == '__main__':
    main()
