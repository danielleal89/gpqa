import os
import sqlite3
from flask import g
_RUNTIME_SCHEMA_READY = False


def _use_cloudflare_d1():
    driver = (os.getenv('DB_DRIVER') or '').strip().lower()
    if driver in {'cloudflare_d1', 'd1'}:
        return True
    return bool(
        (os.getenv('CLOUDFLARE_ACCOUNT_ID') or '').strip()
        and (os.getenv('CLOUDFLARE_D1_DATABASE_ID') or '').strip()
        and (os.getenv('CLOUDFLARE_API_TOKEN') or '').strip()
    )


def using_cloudflare_d1():
    return _use_cloudflare_d1()


def _require_cloudflare_d1():
    if _use_cloudflare_d1():
        return
    raise RuntimeError(
        'Cloudflare D1 nao esta configurado. Defina DB_DRIVER=cloudflare_d1 e as variaveis '
        'CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_D1_DATABASE_ID e CLOUDFLARE_API_TOKEN.'
    )


def is_runtime_schema_ready():
    return _RUNTIME_SCHEMA_READY


def mark_runtime_schema_ready():
    global _RUNTIME_SCHEMA_READY
    _RUNTIME_SCHEMA_READY = True


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        _require_cloudflare_d1()
        try:
            from .cloudflare_d1 import D1Connection
        except ImportError:
            from cloudflare_d1 import D1Connection
        db = g._database = D1Connection.from_env()
    return db


def close_connection(_exception):
    db = getattr(g, '_database', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def init_db():
    global _RUNTIME_SCHEMA_READY
    _require_cloudflare_d1()
    try:
        from .cloudflare_d1 import D1Connection
    except ImportError:
        from cloudflare_d1 import D1Connection
    conn = D1Connection.from_env()

    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                key TEXT,
                birthday TEXT,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                color TEXT,
                photo TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tarefas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_tarefa TEXT NOT NULL,
                descricao TEXT,
                responsavel INTEGER,
                prioridade TEXT DEFAULT 'Media',
                data_inicio TEXT,
                data_fim TEXT,
                arquivado INTEGER DEFAULT 0,
                coluna TEXT DEFAULT 'todo',
                project_id TEXT,
                step_index INTEGER,
                substep_index INTEGER,
                posicao INTEGER,
                FOREIGN KEY (responsavel) REFERENCES users (id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kanban_colunas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                nome TEXT NOT NULL,
                ordem INTEGER NOT NULL,
                cor TEXT NOT NULL DEFAULT '#64748b'
            )
        ''')

        try:
            cursor.execute("ALTER TABLE kanban_colunas ADD COLUMN cor TEXT NOT NULL DEFAULT '#64748b'")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE tarefas ADD COLUMN coluna_id INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE tarefas ADD COLUMN impedido INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE tarefas ADD COLUMN impedimento TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE tarefas ADD COLUMN sprint_id INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE tarefas ADD COLUMN posicao INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE projetos ADD COLUMN status_coluna_id INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE passos ADD COLUMN status_coluna_id INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE subpassos ADD COLUMN status_coluna_id INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE projetos ADD COLUMN project_status TEXT NOT NULL DEFAULT 'not_started'")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute('ALTER TABLE projetos ADD COLUMN created_by_name TEXT')
        except sqlite3.OperationalError:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projetos (
                id TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                descricao TEXT,
                status TEXT,
                status_coluna_id INTEGER,
                project_status TEXT NOT NULL DEFAULT 'not_started',
                created_by_name TEXT,
                created_at TEXT,
                features TEXT,
                passos_ids TEXT,
                logo_path TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS passos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT,
                nome TEXT,
                status TEXT,
                status_coluna_id INTEGER,
                completed_at TEXT,
                ordem INTEGER,
                subpassos_ids TEXT,
                FOREIGN KEY(project_id) REFERENCES projetos(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subpassos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                step_id INTEGER,
                nome TEXT,
                status TEXT,
                status_coluna_id INTEGER,
                completed_at TEXT,
                ordem INTEGER,
                notes TEXT,
                links TEXT,
                images TEXT,
                FOREIGN KEY(step_id) REFERENCES passos(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kanban_task_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                images TEXT NOT NULL DEFAULT '[]',
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(card_id, note),
                FOREIGN KEY(card_id) REFERENCES tarefas(id) ON DELETE CASCADE
            )
        ''')
        try:
            cursor.execute("ALTER TABLE kanban_task_notes ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE kanban_task_notes ADD COLUMN created_by_name TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kanban_sprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                project_id TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                start_date TEXT,
                end_date TEXT,
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')
        try:
            cursor.execute('ALTER TABLE kanban_sprints ADD COLUMN description TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE kanban_sprints ADD COLUMN project_id TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE kanban_sprints ADD COLUMN status TEXT NOT NULL DEFAULT 'planned'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE kanban_sprints ADD COLUMN start_date TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE kanban_sprints ADD COLUMN end_date TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE kanban_sprints ADD COLUMN created_by_name TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_documentations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                title TEXT,
                file_name TEXT,
                file_path TEXT,
                link_url TEXT,
                mime_type TEXT,
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projetos(id) ON DELETE CASCADE
            )
        ''')
        try:
            cursor.execute('ALTER TABLE project_documentations ADD COLUMN title TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE project_documentations ADD COLUMN file_name TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE project_documentations ADD COLUMN file_path TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE project_documentations ADD COLUMN link_url TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE project_documentations ADD COLUMN mime_type TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE project_documentations ADD COLUMN created_by_name TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_modules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                ordem INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(project_id) REFERENCES projetos(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_module_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                item_type TEXT NOT NULL,
                title TEXT,
                file_name TEXT,
                file_path TEXT,
                link_url TEXT,
                mime_type TEXT,
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(module_id) REFERENCES project_modules(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ausencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo INTEGER NOT NULL,
                colaborador INTEGER NOT NULL,
                data_inicio TEXT NOT NULL,
                data_fim TEXT NOT NULL,
                FOREIGN KEY (colaborador) REFERENCES users (id)
            )
        ''')
        conn.commit()

        try:
            from .kanban.models import (
                ensure_impedimento_columns,
                ensure_kanban_columns_table,
                ensure_kanban_task_notes_table,
                ensure_kanban_sprints_table,
            )
            from .projects.models import (
                ensure_project_status_columns,
                ensure_project_documentations_table,
                ensure_project_modules_table,
                ensure_module_items_table,
            )
        except ImportError:
            from kanban.models import (  # type: ignore
                ensure_impedimento_columns,
                ensure_kanban_columns_table,
                ensure_kanban_task_notes_table,
                ensure_kanban_sprints_table,
            )
            from projects.models import (  # type: ignore
                ensure_project_status_columns,
                ensure_project_documentations_table,
                ensure_project_modules_table,
                ensure_module_items_table,
            )

        ensure_impedimento_columns(conn)
        ensure_kanban_columns_table(conn)
        ensure_kanban_task_notes_table(conn)
        ensure_kanban_sprints_table(conn)
        ensure_project_status_columns(conn)
        ensure_project_documentations_table(conn)
        ensure_project_modules_table(conn)
        ensure_module_items_table(conn)
        conn.commit()
        _RUNTIME_SCHEMA_READY = True
    finally:
        try:
            conn.close()
        except Exception:
            pass
