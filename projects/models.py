import json
import os
import uuid
from datetime import datetime
from flask import g, has_app_context

try:
    from ..db import get_db, is_runtime_schema_ready
    from ..runtime_cache import get_cached, set_cached, invalidate_cache, invalidate_cache_prefix
except (ImportError, ValueError):
    try:
        from plm_qa_dashboard.db import get_db, is_runtime_schema_ready
    except ImportError:
        from db import get_db, is_runtime_schema_ready
    try:
        from runtime_cache import get_cached, set_cached, invalidate_cache, invalidate_cache_prefix  # type: ignore
    except ImportError:
        from runtime_cache import get_cached, set_cached, invalidate_cache, invalidate_cache_prefix  # type: ignore


PROJECT_STATUS_OPTIONS = {
    'not_started': {
        'label': 'Não iniciado',
        'theme': {
            'accent': '#94a3b8',
            'accent_text': '#ffffff',
            'surface': '#f8fafc',
            'surface_strong': '#e2e8f0',
            'border': '#cbd5e1',
            'text': '#334155',
            'pill_bg': '#e2e8f0',
            'pill_text': '#475569'
        }
    },
    'in_progress': {
        'label': 'Em andamento',
        'theme': {
            'accent': '#3b82f6',
            'accent_text': '#ffffff',
            'surface': '#eff6ff',
            'surface_strong': '#dbeafe',
            'border': '#93c5fd',
            'text': '#1d4ed8',
            'pill_bg': '#dbeafe',
            'pill_text': '#1d4ed8'
        }
    },
    'completed': {
        'label': 'Concluído',
        'theme': {
            'accent': '#16a34a',
            'accent_text': '#ffffff',
            'surface': '#f0fdf4',
            'surface_strong': '#dcfce7',
            'border': '#86efac',
            'text': '#166534',
            'pill_bg': '#dcfce7',
            'pill_text': '#166534'
        }
    }
}
DEFAULT_PROJECT_STATUS = 'not_started'
PROJECTS_CACHE_KEY = 'projects:all'


def _get_request_cache():
    if not has_app_context():
        return None
    cache = getattr(g, '_projects_model_cache', None)
    if cache is None:
        cache = {}
        g._projects_model_cache = cache
    return cache


def _get_kanban_helpers():
    try:
        from ..kanban.models import (
            ensure_kanban_columns_table,
            get_board_columns,
            get_default_column_slug,
            get_column_by_slug,
            DONE_COLUMN_SLUG
        )
    except (ImportError, ValueError):
        from kanban.models import (  # type: ignore
            ensure_kanban_columns_table,
            get_board_columns,
            get_default_column_slug,
            get_column_by_slug,
            DONE_COLUMN_SLUG
        )

    return (
        ensure_kanban_columns_table,
        get_board_columns,
        get_default_column_slug,
        get_column_by_slug,
        DONE_COLUMN_SLUG
    )


def _get_kanban_card_helpers():
    try:
        from ..kanban.models import create_card
    except (ImportError, ValueError):
        from kanban.models import create_card  # type: ignore

    return create_card


def _table_info(db, table_name):
    return {
        row['name']: dict(row)
        for row in db.execute(f'PRAGMA table_info({table_name})').fetchall()
    }


def _normalize_legacy_status(status_slug):
    status_slug = (status_slug or '').strip()
    legacy_map = {
        'stopped': 'backlog',
        'pending': 'todo',
        'active': 'doing',
        'in_progress': 'doing',
        'completed': 'done'
    }
    return legacy_map.get(status_slug, status_slug)


def _normalize_project_status(status_slug):
    status_slug = (status_slug or '').strip()
    project_status_map = {
        'not_started': 'not_started',
        'in_progress': 'in_progress',
        'completed': 'completed',
        'backlog': 'not_started',
        'todo': 'not_started',
        'doing': 'in_progress',
        'done': 'completed'
    }
    return project_status_map.get(status_slug, DEFAULT_PROJECT_STATUS)


def _format_project_datetime(value):
    if not value:
        return ''

    for fmt in ('%Y-%m-%d %H:%M:%S', '%d/%m/%Y - %H:%M', '%d/%m/%Y %H:%M'):
        try:
            return datetime.strptime(value, fmt).strftime('%d/%m/%Y %H:%M')
        except ValueError:
            continue
    return value


def _format_project_date(value):
    if not value:
        return ''

    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y', '%d/%m/%Y %H:%M', '%d/%m/%Y - %H:%M'):
        try:
            return datetime.strptime(value, fmt).strftime('%d/%m/%Y')
        except ValueError:
            continue
    return value


def get_project_status_options():
    return [
        {'value': value, 'label': data['label'], 'theme': dict(data['theme'])}
        for value, data in PROJECT_STATUS_OPTIONS.items()
    ]


def ensure_project_status_columns(db):
    if is_runtime_schema_ready():
        return
    (
        ensure_kanban_columns_table,
        _get_board_columns,
        _get_default_column_slug,
        _get_column_by_slug,
        _done_column_slug
    ) = _get_kanban_helpers()

    ensure_kanban_columns_table(db)
    changed = False

    projetos_info = _table_info(db, 'projetos')
    if 'project_status' not in projetos_info:
        db.execute(f"ALTER TABLE projetos ADD COLUMN project_status TEXT NOT NULL DEFAULT '{DEFAULT_PROJECT_STATUS}'")
        changed = True
    if 'created_by_name' not in projetos_info:
        db.execute("ALTER TABLE projetos ADD COLUMN created_by_name TEXT")
        changed = True
    if 'logo_path' not in projetos_info:
        db.execute("ALTER TABLE projetos ADD COLUMN logo_path TEXT")
        changed = True

    cursor = db.execute('''
        UPDATE projetos
        SET project_status = CASE COALESCE(project_status, status)
            WHEN 'doing' THEN 'in_progress'
            WHEN 'done' THEN 'completed'
            WHEN 'completed' THEN 'completed'
            WHEN 'in_progress' THEN 'in_progress'
            WHEN 'backlog' THEN 'not_started'
            WHEN 'todo' THEN 'not_started'
            WHEN 'not_started' THEN 'not_started'
            ELSE 'not_started'
        END
        WHERE project_status IS NULL
           OR TRIM(project_status) = ''
           OR project_status NOT IN ('not_started', 'in_progress', 'completed')
    ''')
    if cursor.rowcount > 0:
        changed = True

    cursor = db.execute('''
        UPDATE projetos
        SET created_by_name = 'Não informado'
        WHERE created_by_name IS NULL OR TRIM(created_by_name) = ''
    ''')
    if cursor.rowcount > 0:
        changed = True

    for table_name in ('projetos', 'passos', 'subpassos'):
        table_info = _table_info(db, table_name)
        if 'status_coluna_id' not in table_info:
            db.execute(f'ALTER TABLE {table_name} ADD COLUMN status_coluna_id INTEGER')
            changed = True

        cursor = db.execute(f'''
            UPDATE {table_name}
            SET status = CASE status
                WHEN 'stopped' THEN 'backlog'
                WHEN 'pending' THEN 'todo'
                WHEN 'active' THEN 'doing'
                WHEN 'in_progress' THEN 'doing'
                WHEN 'completed' THEN 'done'
                ELSE status
            END
            WHERE status IN ('stopped', 'pending', 'active', 'in_progress', 'completed')
        ''')
        if cursor.rowcount > 0:
            changed = True

        cursor = db.execute(f'''
            UPDATE {table_name}
            SET status_coluna_id = (
                SELECT kc.id
                FROM kanban_colunas kc
                WHERE kc.slug = {table_name}.status
            )
            WHERE status_coluna_id IS NULL AND status IS NOT NULL
        ''')
        if cursor.rowcount > 0:
            changed = True

    default_column = db.execute(
        'SELECT id, slug FROM kanban_colunas ORDER BY ordem, id LIMIT 1'
    ).fetchone()
    if default_column:
        for table_name in ('projetos', 'passos', 'subpassos'):
            cursor = db.execute(
                f'UPDATE {table_name} SET status_coluna_id = ? WHERE status_coluna_id IS NULL',
                (default_column['id'],)
            )
            if cursor.rowcount > 0:
                changed = True

            cursor = db.execute(f'''
                UPDATE {table_name}
                SET status = (
                    SELECT kc.slug
                    FROM kanban_colunas kc
                    WHERE kc.id = {table_name}.status_coluna_id
                )
                WHERE status_coluna_id IS NOT NULL
            ''')
            if cursor.rowcount > 0:
                changed = True

    if changed:
        db.commit()


def get_status_columns():
    cache = _get_request_cache()
    if cache is not None and 'status_columns' in cache:
        return cache['status_columns']
    db = get_db()
    ensure_project_status_columns(db)
    _, get_board_columns, _, _, _ = _get_kanban_helpers()
    columns = get_board_columns()
    if cache is not None:
        cache['status_columns'] = columns
    return columns


def ensure_kanban_task_notes_table(db):
    if is_runtime_schema_ready():
        return
    notes_info = _table_info(db, 'kanban_task_notes')
    db.execute('''
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
    if notes_info and 'images' not in notes_info:
        db.execute("ALTER TABLE kanban_task_notes ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
    if notes_info and 'created_by_name' not in notes_info:
        db.execute("ALTER TABLE kanban_task_notes ADD COLUMN created_by_name TEXT NOT NULL DEFAULT ''")
    db.commit()


def ensure_project_documentations_table(db):
    if is_runtime_schema_ready():
        return
    docs_info = _table_info(db, 'project_documentations')
    db.execute('''
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
    if docs_info and 'title' not in docs_info:
        db.execute('ALTER TABLE project_documentations ADD COLUMN title TEXT')
    if docs_info and 'file_name' not in docs_info:
        db.execute('ALTER TABLE project_documentations ADD COLUMN file_name TEXT')
    if docs_info and 'file_path' not in docs_info:
        db.execute('ALTER TABLE project_documentations ADD COLUMN file_path TEXT')
    if docs_info and 'link_url' not in docs_info:
        db.execute('ALTER TABLE project_documentations ADD COLUMN link_url TEXT')
    if docs_info and 'mime_type' not in docs_info:
        db.execute('ALTER TABLE project_documentations ADD COLUMN mime_type TEXT')
    if docs_info and 'created_by_name' not in docs_info:
        db.execute("ALTER TABLE project_documentations ADD COLUMN created_by_name TEXT NOT NULL DEFAULT ''")
    db.commit()


def ensure_project_modules_table(db):
    if is_runtime_schema_ready():
        return
    db.execute('''
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
    db.commit()


def ensure_module_items_table(db):
    if is_runtime_schema_ready():
        return
    db.execute('''
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
    db.commit()


MODULE_ITEM_CATEGORIES = {
    'ux': 'Telas de UX',
    'doc': 'Documentações',
    'codigo': 'Códigos',
    'extras': 'Extras'
}


def get_project_modules(project_id):
    db = get_db()
    ensure_project_modules_table(db)
    ensure_module_items_table(db)

    module_rows = db.execute(
        'SELECT * FROM project_modules WHERE project_id = ? ORDER BY ordem, id',
        (project_id,)
    ).fetchall()
    modules = [dict(row) for row in module_rows]
    if not modules:
        return []

    module_ids = [module['id'] for module in modules]
    placeholders = ','.join('?' for _ in module_ids)
    count_rows = db.execute(
        f'''
        SELECT module_id, category, COUNT(*) as total
        FROM project_module_items
        WHERE module_id IN ({placeholders})
        GROUP BY module_id, category
        ''',
        tuple(module_ids)
    ).fetchall()

    counts_by_module = {module_id: {} for module_id in module_ids}
    for row in count_rows:
        counts_by_module[row['module_id']][row['category']] = row['total']

    for module in modules:
        module['created_at_display'] = _format_project_datetime(module.get('created_at'))
        module['category_counts'] = counts_by_module.get(module['id'], {})
        module['item_total'] = sum(module['category_counts'].values())

    return modules


def create_module(project_id, name, description, created_by_name=''):
    db = get_db()
    ensure_project_modules_table(db)

    clean_name = (name or '').strip()
    if not clean_name:
        return {'success': False, 'status': 'invalid_name'}

    project = db.execute('SELECT id FROM projetos WHERE id = ? LIMIT 1', (project_id,)).fetchone()
    if not project:
        return {'success': False, 'status': 'missing_project'}

    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ordem_row = db.execute(
        'SELECT COALESCE(MAX(ordem), -1) + 1 as next_ordem FROM project_modules WHERE project_id = ?',
        (project_id,)
    ).fetchone()
    next_ordem = ordem_row['next_ordem'] if ordem_row else 0

    cursor = db.execute(
        '''
        INSERT INTO project_modules (project_id, name, description, created_by_name, created_at, ordem)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (project_id, clean_name, (description or '').strip(), created_by_name or '', created_at, next_ordem)
    )
    module_id = getattr(cursor, 'lastrowid', None)
    if not module_id:
        module_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    return {'success': True, 'status': 'created', 'module_id': module_id}


def get_module(project_id, module_id):
    db = get_db()
    ensure_project_modules_table(db)
    ensure_module_items_table(db)

    row = db.execute(
        'SELECT * FROM project_modules WHERE id = ? AND project_id = ? LIMIT 1',
        (module_id, project_id)
    ).fetchone()
    if not row:
        return None

    module = dict(row)
    module['created_at_display'] = _format_project_datetime(module.get('created_at'))

    item_rows = db.execute(
        '''
        SELECT id, category, item_type, title, file_name, file_path, link_url, mime_type, created_by_name, created_at
        FROM project_module_items
        WHERE module_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        ''',
        (module_id,)
    ).fetchall()

    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    items_by_category = {category: [] for category in MODULE_ITEM_CATEGORIES}
    for item_row in item_rows:
        item = dict(item_row)
        item['created_at_display'] = _format_project_datetime(item.get('created_at'))
        file_path = item.get('file_path') or ''
        file_extension = os.path.splitext(file_path)[1].lower()
        item['is_image'] = item.get('item_type') == 'file' and file_extension in image_extensions
        items_by_category.setdefault(item.get('category'), []).append(item)

    module['items_by_category'] = items_by_category
    return module


def delete_module(project_id, module_id):
    db = get_db()
    ensure_project_modules_table(db)
    ensure_module_items_table(db)

    module = db.execute(
        'SELECT id FROM project_modules WHERE id = ? AND project_id = ? LIMIT 1',
        (module_id, project_id)
    ).fetchone()
    if not module:
        return {'success': False, 'status': 'missing'}

    file_rows = db.execute(
        "SELECT file_path FROM project_module_items WHERE module_id = ? AND file_path IS NOT NULL AND file_path != ''",
        (module_id,)
    ).fetchall()
    file_paths = [row['file_path'] for row in file_rows]

    db.execute('DELETE FROM project_module_items WHERE module_id = ?', (module_id,))
    db.execute('DELETE FROM project_modules WHERE id = ? AND project_id = ?', (module_id, project_id))
    db.commit()

    return {'success': True, 'status': 'deleted', 'file_paths': file_paths}


def add_module_item(project_id, module_id, category, file_entries=None, link_url=None, link_title=None, created_by_name=''):
    db = get_db()
    ensure_module_items_table(db)

    if category not in MODULE_ITEM_CATEGORIES:
        return {'success': False, 'status': 'invalid_category'}

    module = db.execute(
        'SELECT id FROM project_modules WHERE id = ? AND project_id = ? LIMIT 1',
        (module_id, project_id)
    ).fetchone()
    if not module:
        return {'success': False, 'status': 'missing_module'}

    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    inserted_count = 0

    for file_entry in file_entries or []:
        db.execute(
            '''
            INSERT INTO project_module_items (
                module_id, category, item_type, title, file_name, file_path, link_url, mime_type, created_by_name, created_at
            ) VALUES (?, ?, 'file', ?, ?, ?, NULL, ?, ?, ?)
            ''',
            (
                module_id,
                category,
                file_entry.get('title') or file_entry.get('file_name') or 'Arquivo',
                file_entry.get('file_name') or '',
                file_entry.get('file_path') or '',
                file_entry.get('mime_type') or '',
                created_by_name or '',
                created_at
            )
        )
        inserted_count += 1

    clean_link_url = (link_url or '').strip()
    if clean_link_url:
        if '://' not in clean_link_url:
            clean_link_url = f'https://{clean_link_url}'
        db.execute(
            '''
            INSERT INTO project_module_items (
                module_id, category, item_type, title, file_name, file_path, link_url, mime_type, created_by_name, created_at
            ) VALUES (?, ?, 'link', ?, NULL, NULL, ?, NULL, ?, ?)
            ''',
            (
                module_id,
                category,
                (link_title or '').strip() or clean_link_url,
                clean_link_url,
                created_by_name or '',
                created_at
            )
        )
        inserted_count += 1

    db.commit()
    if inserted_count == 0:
        return {'success': False, 'status': 'empty'}
    return {'success': True, 'status': 'created'}


def delete_module_item(project_id, module_id, item_id):
    db = get_db()
    ensure_module_items_table(db)

    module = db.execute(
        'SELECT id FROM project_modules WHERE id = ? AND project_id = ? LIMIT 1',
        (module_id, project_id)
    ).fetchone()
    if not module:
        return {'success': False, 'status': 'missing_module'}

    item = db.execute(
        'SELECT id, file_path FROM project_module_items WHERE id = ? AND module_id = ? LIMIT 1',
        (item_id, module_id)
    ).fetchone()
    if not item:
        return {'success': False, 'status': 'missing'}

    db.execute('DELETE FROM project_module_items WHERE id = ? AND module_id = ?', (item_id, module_id))
    db.commit()

    item_data = dict(item)
    return {'success': True, 'status': 'deleted', 'file_path': item_data.get('file_path') or ''}


def get_module_counts_by_project(project_ids):
    if not project_ids:
        return {}
    db = get_db()
    ensure_project_modules_table(db)
    placeholders = ','.join('?' for _ in project_ids)
    rows = db.execute(
        f'''
        SELECT project_id, COUNT(*) as total
        FROM project_modules
        WHERE project_id IN ({placeholders})
        GROUP BY project_id
        ''',
        tuple(project_ids)
    ).fetchall()
    return {row['project_id']: row['total'] for row in rows}


def _get_project_documentations(project_id):
    db = get_db()
    ensure_project_documentations_table(db)
    rows = db.execute(
        '''
        SELECT id, item_type, title, file_name, file_path, link_url, mime_type, created_by_name, created_at
        FROM project_documentations
        WHERE project_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        ''',
        (project_id,)
    ).fetchall()

    documentation_items = []
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    for row in rows:
        item = dict(row)
        item['created_at_display'] = _format_project_datetime(item.get('created_at'))
        file_path = item.get('file_path') or ''
        file_extension = os.path.splitext(file_path)[1].lower()
        item['is_image'] = item.get('item_type') == 'file' and file_extension in image_extensions
        documentation_items.append(item)
    return documentation_items


def _get_project_kanban_cards(project_id):
    db = get_db()
    ensure_kanban_task_notes_table(db)
    rows = db.execute('''
        SELECT
            t.id,
            t.nome_tarefa,
            t.descricao,
            t.responsavel,
            t.prioridade,
            t.data_inicio,
            t.data_fim,
            t.data_entrega,
            t.coluna,
            t.step_index,
            t.substep_index,
            t.impedido,
            t.impedimento,
            kc.nome AS coluna_nome,
            u.name AS responsavel_nome,
            u.photo AS responsavel_photo,
            u.color AS responsavel_color
        FROM tarefas t
        LEFT JOIN kanban_colunas kc ON kc.id = t.coluna_id
        LEFT JOIN users u ON u.id = t.responsavel
        WHERE t.project_id = ?
    ''', (project_id,)).fetchall()

    cards_by_ref = {}
    for row in rows:
        card = dict(row)
        card['id'] = str(card['id'])
        card['title'] = card.pop('nome_tarefa')
        card['description'] = card.pop('descricao')
        card['column_name'] = card.pop('coluna_nome') or card.get('coluna') or 'Sem coluna'
        card['assigned_to_name'] = card.pop('responsavel_nome') or 'Nao informado'
        card['assigned_to_id'] = str(card.pop('responsavel')) if card.get('responsavel') else ''
        card['assigned_to_photo'] = card.pop('responsavel_photo') or ''
        card['assigned_to_color'] = card.pop('responsavel_color') or '#6366f1'
        card['delivery_date_display'] = _format_project_date(card.get('data_entrega'))
        card['start_date_display'] = _format_project_datetime(card.get('data_inicio'))
        card['end_date_display'] = _format_project_datetime(card.get('data_fim'))
        card['impedido'] = bool(card.get('impedido'))
        cards_by_ref[(card['step_index'], card['substep_index'])] = card
    return cards_by_ref


def _get_kanban_card_notes(card_ids):
    if not card_ids:
        return {}

    db = get_db()
    ensure_kanban_task_notes_table(db)
    placeholders = ','.join('?' for _ in card_ids)
    rows = db.execute(
        f'''
        SELECT id, card_id, note, created_by_name, created_at
             , images
        FROM kanban_task_notes
        WHERE card_id IN ({placeholders})
        ORDER BY id DESC
        ''',
        tuple(card_ids)
    ).fetchall()

    notes_by_card = {}
    for row in rows:
        note = dict(row)
        note['created_at_display'] = _format_project_datetime(note.get('created_at'))
        try:
            note['images'] = json.loads(note['images']) if note.get('images') else []
        except Exception:
            note['images'] = []
        notes_by_card.setdefault(str(note['card_id']), []).append(note)
    return notes_by_card


def _get_preferred_status_slug(preferred_slug):
    _, _, get_default_column_slug, get_column_by_slug, _ = _get_kanban_helpers()
    if get_column_by_slug(preferred_slug):
        return preferred_slug
    return get_default_column_slug()


def _attach_status_metadata(entity, status_columns_by_slug):
    status_slug = _normalize_legacy_status(entity.get('status'))
    column = status_columns_by_slug.get(status_slug)
    if not column and status_columns_by_slug:
        column = next(iter(status_columns_by_slug.values()))
        status_slug = column['slug']

    entity['status'] = status_slug
    entity['status_slug'] = status_slug
    entity['status_name'] = column['name'] if column else status_slug
    entity['status_coluna_id'] = column['id'] if column else entity.get('status_coluna_id')
    entity['status_theme'] = dict(column['theme']) if column and column.get('theme') else {}
    return entity


def _attach_project_metadata(project):
    project_status = _normalize_project_status(project.get('project_status') or project.get('status'))
    project_meta = PROJECT_STATUS_OPTIONS.get(project_status, PROJECT_STATUS_OPTIONS[DEFAULT_PROJECT_STATUS])
    created_by_name = (project.get('created_by_name') or 'Não informado').strip() or 'Não informado'

    project['project_status'] = project_status
    project['project_status_name'] = project_meta['label']
    project['project_status_theme'] = dict(project_meta['theme'])
    project['created_by_name'] = created_by_name
    project['created_at_display'] = _format_project_datetime(project.get('created_at'))
    return project


def _ensure_kanban_task_sync_columns(db):
    if is_runtime_schema_ready():
        return
    tarefas_info = _table_info(db, 'tarefas')
    changed = False

    if 'data_entrega' not in tarefas_info:
        db.execute('ALTER TABLE tarefas ADD COLUMN data_entrega TEXT')
        changed = True

    if changed:
        db.commit()


def _sync_linked_kanban_cards(project_id, status_slug, step_index=None, substep_index=None):
    db = get_db()
    ensure_project_status_columns(db)
    _ensure_kanban_task_sync_columns(db)

    _, _, _, get_column_by_slug, done_column_slug = _get_kanban_helpers()
    status_column = get_column_by_slug(status_slug)
    if not status_column:
        return 0

    if step_index is None and substep_index is None:
        where_clause = 'project_id = ? AND step_index IS NULL AND substep_index IS NULL'
        where_params = (project_id,)
    elif substep_index is None:
        where_clause = 'project_id = ? AND step_index = ? AND substep_index = ?'
        where_params = (project_id, step_index, -1)
    else:
        where_clause = 'project_id = ? AND step_index = ? AND substep_index = ?'
        where_params = (project_id, step_index, substep_index)

    if status_slug == done_column_slug:
        delivered_at = datetime.now().strftime('%Y-%m-%d')
        cursor = db.execute(
            f'UPDATE tarefas SET coluna = ?, coluna_id = ?, data_entrega = ? WHERE {where_clause}',
            (status_column['slug'], status_column['id'], delivered_at, *where_params)
        )
    else:
        cursor = db.execute(
            f'UPDATE tarefas SET coluna = ?, coluna_id = ?, data_entrega = NULL WHERE {where_clause}',
            (status_column['slug'], status_column['id'], *where_params)
        )

    return cursor.rowcount


def _sync_linked_kanban_step_title(project_id, step_index, step_name):
    db = get_db()
    cursor = db.execute(
        '''
        UPDATE tarefas
        SET nome_tarefa = ?
        WHERE project_id = ? AND step_index = ? AND substep_index = ?
        ''',
        (step_name, project_id, step_index, -1)
    )
    return cursor.rowcount


def _sync_linked_kanban_substep_title(project_id, step_index, substep_index, substep_name):
    db = get_db()
    cursor = db.execute(
        '''
        UPDATE tarefas
        SET nome_tarefa = ?
        WHERE project_id = ? AND step_index = ? AND substep_index = ?
        ''',
        (substep_name, project_id, step_index, substep_index)
    )
    return cursor.rowcount


def _get_project_kanban_links(project_id):
    db = get_db()
    rows = db.execute(
        'SELECT id, step_index, substep_index FROM tarefas WHERE project_id = ?',
        (project_id,)
    ).fetchall()
    return {
        (row['step_index'], row['substep_index']): str(row['id'])
        for row in rows
    }


def load_projects():
    cached_projects = get_cached(PROJECTS_CACHE_KEY)
    if cached_projects is not None:
        return cached_projects

    db = get_db()
    status_columns = get_status_columns()
    status_columns_by_slug = {column['slug']: column for column in status_columns}
    cursor = db.execute('SELECT * FROM projetos ORDER BY created_at DESC')
    rows = cursor.fetchall()
    project_ids = [row['id'] for row in rows]

    steps_by_project = {}
    substeps_by_step = {}
    if project_ids:
        project_placeholders = ','.join('?' for _ in project_ids)
        steps_rows = db.execute(
            f'SELECT * FROM passos WHERE project_id IN ({project_placeholders}) ORDER BY project_id, ordem',
            tuple(project_ids)
        ).fetchall()
        step_ids = [row['id'] for row in steps_rows]

        if step_ids:
            step_placeholders = ','.join('?' for _ in step_ids)
            sub_rows = db.execute(
                f'SELECT * FROM subpassos WHERE step_id IN ({step_placeholders}) ORDER BY step_id, ordem',
                tuple(step_ids)
            ).fetchall()
            for sub_row in sub_rows:
                sub = dict(sub_row)
                sub['name'] = sub['nome']
                _attach_status_metadata(sub, status_columns_by_slug)
                try:
                    sub['links'] = json.loads(sub['links']) if sub['links'] else []
                except Exception:
                    sub['links'] = []
                try:
                    sub['images'] = json.loads(sub['images']) if sub['images'] else []
                except Exception:
                    sub['images'] = []
                if not sub['completed_at']:
                    sub.pop('completed_at', None)
                substeps_by_step.setdefault(sub['step_id'], []).append(sub)

        for s_row in steps_rows:
            step = dict(s_row)
            step['name'] = step['nome']
            _attach_status_metadata(step, status_columns_by_slug)
            step['substeps'] = substeps_by_step.get(step['id'], [])
            if not step['completed_at']:
                step.pop('completed_at', None)
            steps_by_project.setdefault(step['project_id'], []).append(step)

    projects = []
    for row in rows:
        proj = dict(row)
        proj['name'] = proj['nome']  # Map DB column 'nome' to 'name'
        proj['description'] = proj['descricao']  # Map DB column 'descricao' to 'description'
        _attach_project_metadata(proj)
        # Parse features
        try:
            proj['features'] = json.loads(proj['features']) if proj['features'] else []
        except Exception:
            proj['features'] = []
        proj['steps'] = steps_by_project.get(proj['id'], [])

        projects.append(proj)

    return set_cached(PROJECTS_CACHE_KEY, projects, ttl_seconds=5)


def _invalidate_project_caches(project_id=None):
    invalidate_cache(PROJECTS_CACHE_KEY)
    invalidate_cache_prefix('kanban:')
    if project_id:
        invalidate_cache(f'project:{project_id}')


def save_projects(_projects):
    pass


def create_project(name, description, steps_data, created_by_name=None):
    """
    steps_data: Lista de dicionários, ex:
    [
        {
            'name': 'Passo 1',
            'substeps': ['Sub 1', 'Sub 2']
        }
    ]
    """
    db = get_db()
    ensure_project_status_columns(db)
    p_id = str(uuid.uuid4())
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    creator_name = (created_by_name or 'Não informado').strip() or 'Não informado'
    project_status_slug = _get_preferred_status_slug('backlog')
    task_status_slug = _get_preferred_status_slug('todo')
    status_columns = {column['slug']: column for column in get_status_columns()}
    project_status_column = status_columns.get(project_status_slug)
    task_status_column = status_columns.get(task_status_slug)

    db.execute(
        '''
        INSERT INTO projetos (
            id, nome, descricao, status, status_coluna_id, project_status, created_by_name, created_at, features, passos_ids
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            p_id,
            name,
            description,
            project_status_slug,
            project_status_column['id'] if project_status_column else None,
            DEFAULT_PROJECT_STATUS,
            creator_name,
            created_at,
            '[]',
            ''
        )
    )

    step_ids = []
    for i, step_data in enumerate(steps_data):
        step_cursor = db.execute(
            'INSERT INTO passos (project_id, nome, status, status_coluna_id, ordem, subpassos_ids) VALUES (?, ?, ?, ?, ?, ?)',
            (p_id, step_data['name'], task_status_slug, task_status_column['id'] if task_status_column else None, i, '')
        )
        step_id = getattr(step_cursor, 'lastrowid', None)
        if not step_id:
            step_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        step_ids.append(str(step_id))

        substep_ids = []
        substeps_list = step_data.get('substeps', [])
        for j, sub_name in enumerate(substeps_list):
            sub_cursor = db.execute(
                'INSERT INTO subpassos (step_id, nome, status, status_coluna_id, ordem, notes, links, images) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (step_id, sub_name, task_status_slug, task_status_column['id'] if task_status_column else None, j, '', '[]', '[]')
            )
            sub_id = getattr(sub_cursor, 'lastrowid', None)
            if not sub_id:
                sub_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            substep_ids.append(str(sub_id))

        if substep_ids:
            db.execute('UPDATE passos SET subpassos_ids = ? WHERE id = ?', (','.join(substep_ids), step_id))

    if step_ids:
        db.execute('UPDATE projetos SET passos_ids = ? WHERE id = ?', (','.join(step_ids), p_id))

    db.commit()
    _invalidate_project_caches(p_id)
    return get_project(p_id)


def update_project_logo(project_id, logo_path):
    db = get_db()
    ensure_project_status_columns(db)
    db.execute('UPDATE projetos SET logo_path = ? WHERE id = ?', (logo_path, project_id))
    db.commit()
    _invalidate_project_caches(project_id)


def get_project(project_id):
    cached_project = get_cached(f'project:{project_id}')
    if cached_project is not None:
        return cached_project

    db = get_db()
    status_columns = get_status_columns()
    status_columns_by_slug = {column['slug']: column for column in status_columns}
    row = db.execute('SELECT * FROM projetos WHERE id = ?', (project_id,)).fetchone()
    if not row:
        return None

    proj = dict(row)
    proj['name'] = proj['nome']  # Map DB column 'nome' to 'name'
    proj['description'] = proj['descricao']  # Map DB column 'descricao' to 'description'
    proj['documentation_items'] = _get_project_documentations(project_id)
    _attach_project_metadata(proj)
    kanban_links = _get_project_kanban_links(project_id)
    kanban_cards = _get_project_kanban_cards(project_id)
    kanban_notes = _get_kanban_card_notes([int(card_id) for card_id in kanban_links.values()])
    try:
        proj['features'] = json.loads(proj['features']) if proj['features'] else []
    except Exception:
        proj['features'] = []

    steps_rows = db.execute('SELECT * FROM passos WHERE project_id = ? ORDER BY ordem', (project_id,)).fetchall()
    step_ids = [step_row['id'] for step_row in steps_rows]
    substeps_by_step = {}
    if step_ids:
        step_placeholders = ','.join('?' for _ in step_ids)
        sub_rows = db.execute(
            f'SELECT * FROM subpassos WHERE step_id IN ({step_placeholders}) ORDER BY step_id, ordem',
            tuple(step_ids)
        ).fetchall()
        for sub_row in sub_rows:
            sub = dict(sub_row)
            sub['name'] = sub['nome']
            _attach_status_metadata(sub, status_columns_by_slug)
            try:
                sub['links'] = json.loads(sub['links']) if sub['links'] else []
            except Exception:
                sub['links'] = []
            try:
                sub['images'] = json.loads(sub['images']) if sub['images'] else []
            except Exception:
                sub['images'] = []
            if not sub['completed_at']:
                sub.pop('completed_at', None)
            substeps_by_step.setdefault(sub['step_id'], []).append(sub)

    proj['steps'] = []
    for s_row in steps_rows:
        step = dict(s_row)
        step['name'] = step['nome']  # Map DB column 'nome' to 'name'
        _attach_status_metadata(step, status_columns_by_slug)
        step['kanban_card_id'] = kanban_links.get((step['ordem'], -1))
        step['has_kanban_card'] = bool(step['kanban_card_id'])
        step['kanban_card'] = kanban_cards.get((step['ordem'], -1))
        step['kanban_notes'] = kanban_notes.get(step['kanban_card_id'], [])
        step['kanban_notes_count'] = len(step['kanban_notes'])
        step['can_add_kanban_note'] = step['has_kanban_card'] and step['kanban_notes_count'] < 10

        step['substeps'] = []
        for sub in substeps_by_step.get(step['id'], []):
            sub['kanban_card_id'] = kanban_links.get((step['ordem'], sub['ordem']))
            sub['has_kanban_card'] = bool(sub['kanban_card_id'])
            sub['kanban_card'] = kanban_cards.get((step['ordem'], sub['ordem']))
            sub['kanban_notes'] = kanban_notes.get(sub['kanban_card_id'], [])
            sub['kanban_notes_count'] = len(sub['kanban_notes'])
            sub['can_add_kanban_note'] = sub['has_kanban_card'] and sub['kanban_notes_count'] < 10
            step['substeps'].append(sub)

        if not step['completed_at']:
            step.pop('completed_at', None)

        proj['steps'].append(step)

    return set_cached(f'project:{project_id}', proj, ttl_seconds=5)


def add_project_documentation(project_id, file_entries=None, link_url=None, link_title=None, created_by_name=''):
    db = get_db()
    ensure_project_documentations_table(db)

    project = db.execute('SELECT id FROM projetos WHERE id = ? LIMIT 1', (project_id,)).fetchone()
    if not project:
        return {'success': False, 'status': 'missing_project'}

    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    inserted_count = 0

    for file_entry in file_entries or []:
        db.execute(
            '''
            INSERT INTO project_documentations (
                project_id, item_type, title, file_name, file_path, link_url, mime_type, created_by_name, created_at
            ) VALUES (?, 'file', ?, ?, ?, NULL, ?, ?, ?)
            ''',
            (
                project_id,
                file_entry.get('title') or file_entry.get('file_name') or 'Arquivo',
                file_entry.get('file_name') or '',
                file_entry.get('file_path') or '',
                file_entry.get('mime_type') or '',
                created_by_name or '',
                created_at
            )
        )
        inserted_count += 1

    clean_link_url = (link_url or '').strip()
    if clean_link_url:
        if '://' not in clean_link_url:
            clean_link_url = f'https://{clean_link_url}'
        db.execute(
            '''
            INSERT INTO project_documentations (
                project_id, item_type, title, file_name, file_path, link_url, mime_type, created_by_name, created_at
            ) VALUES (?, 'link', ?, NULL, NULL, ?, NULL, ?, ?)
            ''',
            (
                project_id,
                (link_title or '').strip() or clean_link_url,
                clean_link_url,
                created_by_name or '',
                created_at
            )
        )
        inserted_count += 1

    db.commit()
    _invalidate_project_caches(project_id)
    if inserted_count == 0:
        return {'success': False, 'status': 'empty'}
    return {'success': True, 'status': 'created'}


def delete_project_documentation(project_id, documentation_id):
    db = get_db()
    ensure_project_documentations_table(db)

    item = db.execute(
        '''
        SELECT id, project_id, item_type, file_path
        FROM project_documentations
        WHERE id = ? AND project_id = ?
        LIMIT 1
        ''',
        (documentation_id, project_id)
    ).fetchone()

    if not item:
        return {'success': False, 'status': 'missing'}

    db.execute(
        'DELETE FROM project_documentations WHERE id = ? AND project_id = ?',
        (documentation_id, project_id)
    )
    db.commit()
    _invalidate_project_caches(project_id)

    item_data = dict(item)
    return {
        'success': True,
        'status': 'deleted',
        'item_type': item_data.get('item_type') or '',
        'file_path': item_data.get('file_path') or ''
    }


def update_project_step(project_id, step_index, status, sync_kanban=True):
    db = get_db()
    ensure_project_status_columns(db)
    step = db.execute('SELECT id FROM passos WHERE project_id = ? AND ordem = ?', (project_id, step_index)).fetchone()

    if step:
        _, _, _, get_column_by_slug, done_column_slug = _get_kanban_helpers()
        status_slug = _normalize_legacy_status(status)
        status_column = get_column_by_slug(status_slug)
        if not status_column:
            return None

        completed_at = datetime.now().strftime('%d/%m/%Y - %H:%M') if status_slug == done_column_slug else None

        if status_slug == done_column_slug:
            db.execute(
                'UPDATE passos SET status = ?, status_coluna_id = ?, completed_at = ? WHERE id = ?',
                (status_slug, status_column['id'], completed_at, step['id'])
            )
        else:
            db.execute(
                'UPDATE passos SET status = ?, status_coluna_id = ?, completed_at = NULL WHERE id = ?',
                (status_slug, status_column['id'], step['id'])
            )
        if sync_kanban:
            _sync_linked_kanban_cards(project_id, status_slug, step_index=step_index)
        db.commit()
        _invalidate_project_caches(project_id)
        return get_project(project_id)
    return None


def update_project_substep(project_id, step_index, substep_index, status, sync_kanban=True):
    db = get_db()
    ensure_project_status_columns(db)
    step = db.execute('SELECT id FROM passos WHERE project_id = ? AND ordem = ?', (project_id, step_index)).fetchone()
    if step:
        substep = db.execute('SELECT id FROM subpassos WHERE step_id = ? AND ordem = ?', (step['id'], substep_index)).fetchone()
        if substep:
            _, _, _, get_column_by_slug, done_column_slug = _get_kanban_helpers()
            status_slug = _normalize_legacy_status(status)
            status_column = get_column_by_slug(status_slug)
            if not status_column:
                return None

            completed_at = datetime.now().strftime('%d/%m/%Y - %H:%M') if status_slug == done_column_slug else None
            if status_slug == done_column_slug:
                db.execute(
                    'UPDATE subpassos SET status = ?, status_coluna_id = ?, completed_at = ? WHERE id = ?',
                    (status_slug, status_column['id'], completed_at, substep['id'])
                )
            else:
                db.execute(
                    'UPDATE subpassos SET status = ?, status_coluna_id = ?, completed_at = NULL WHERE id = ?',
                    (status_slug, status_column['id'], substep['id'])
                )
            if sync_kanban:
                _sync_linked_kanban_cards(project_id, status_slug, step_index=step_index, substep_index=substep_index)
            db.commit()
            _invalidate_project_caches(project_id)
            return get_project(project_id)
    return None


def update_project_status(project_id, status):
    db = get_db()
    ensure_project_status_columns(db)
    status_slug = _normalize_project_status(status)
    if status_slug not in PROJECT_STATUS_OPTIONS:
        return None

    db.execute(
        'UPDATE projetos SET project_status = ? WHERE id = ?',
        (status_slug, project_id)
    )
    db.commit()
    _invalidate_project_caches(project_id)
    return get_project(project_id)


def update_project_name(project_id, project_name):
    db = get_db()
    clean_name = (project_name or '').strip()
    if not clean_name:
        return None

    db.execute(
        'UPDATE projetos SET nome = ? WHERE id = ?',
        (clean_name, project_id)
    )
    db.commit()
    _invalidate_project_caches(project_id)
    return get_project(project_id)


def delete_project_step(project_id, step_index):
    db = get_db()
    ensure_project_status_columns(db)
    ensure_kanban_task_notes_table(db)

    step = db.execute(
        'SELECT id, nome, ordem FROM passos WHERE project_id = ? AND ordem = ?',
        (project_id, step_index)
    ).fetchone()
    if not step:
        return {'success': False, 'status': 'missing_step'}

    substeps = db.execute(
        'SELECT id, nome, images FROM subpassos WHERE step_id = ? ORDER BY ordem',
        (step['id'],)
    ).fetchall()
    deleted_substep_names = [row['nome'] for row in substeps]
    file_paths_to_delete = []

    for substep in substeps:
        try:
            image_paths = json.loads(substep['images']) if substep['images'] else []
        except Exception:
            image_paths = []
        file_paths_to_delete.extend(path for path in image_paths if path)

    linked_cards = db.execute(
        'SELECT id FROM tarefas WHERE project_id = ? AND step_index = ?',
        (project_id, step_index)
    ).fetchall()
    linked_card_ids = [row['id'] for row in linked_cards]
    if linked_card_ids:
        placeholders = ','.join('?' for _ in linked_card_ids)
        note_rows = db.execute(
            f'''
            SELECT images
            FROM kanban_task_notes
            WHERE card_id IN ({placeholders})
            ''',
            tuple(linked_card_ids)
        ).fetchall()
        for note_row in note_rows:
            try:
                image_paths = json.loads(note_row['images']) if note_row['images'] else []
            except Exception:
                image_paths = []
            file_paths_to_delete.extend(path for path in image_paths if path)

        db.execute(
            f'DELETE FROM kanban_task_notes WHERE card_id IN ({placeholders})',
            tuple(linked_card_ids)
        )

    db.execute(
        'DELETE FROM tarefas WHERE project_id = ? AND step_index = ?',
        (project_id, step_index)
    )
    db.execute('DELETE FROM subpassos WHERE step_id = ?', (step['id'],))
    db.execute('DELETE FROM passos WHERE id = ?', (step['id'],))
    db.execute(
        'UPDATE passos SET ordem = ordem - 1 WHERE project_id = ? AND ordem > ?',
        (project_id, step_index)
    )
    db.execute(
        'UPDATE tarefas SET step_index = step_index - 1 WHERE project_id = ? AND step_index > ?',
        (project_id, step_index)
    )

    remaining_steps = db.execute(
        'SELECT id FROM passos WHERE project_id = ? ORDER BY ordem',
        (project_id,)
    ).fetchall()
    ids_str = ','.join(str(row['id']) for row in remaining_steps)
    db.execute('UPDATE projetos SET passos_ids = ? WHERE id = ?', (ids_str, project_id))

    db.commit()
    _invalidate_project_caches(project_id)
    return {
        'success': True,
        'status': 'deleted',
        'deleted_task_name': step['nome'],
        'deleted_substep_names': deleted_substep_names,
        'deleted_file_paths': file_paths_to_delete,
    }


def delete_project_substep(project_id, step_index, substep_index):
    db = get_db()
    ensure_project_status_columns(db)
    ensure_kanban_task_notes_table(db)

    step = db.execute(
        'SELECT id FROM passos WHERE project_id = ? AND ordem = ?',
        (project_id, step_index)
    ).fetchone()
    if not step:
        return {'success': False, 'status': 'missing_step'}

    substep = db.execute(
        'SELECT id, nome, images FROM subpassos WHERE step_id = ? AND ordem = ?',
        (step['id'], substep_index)
    ).fetchone()
    if not substep:
        return {'success': False, 'status': 'missing_substep'}

    file_paths_to_delete = []
    try:
        image_paths = json.loads(substep['images']) if substep['images'] else []
    except Exception:
        image_paths = []
    file_paths_to_delete.extend(path for path in image_paths if path)

    linked_card = db.execute(
        '''
        SELECT id
        FROM tarefas
        WHERE project_id = ? AND step_index = ? AND substep_index = ?
        LIMIT 1
        ''',
        (project_id, step_index, substep_index)
    ).fetchone()
    if linked_card:
        note_rows = db.execute(
            'SELECT images FROM kanban_task_notes WHERE card_id = ?',
            (linked_card['id'],)
        ).fetchall()
        for note_row in note_rows:
            try:
                note_image_paths = json.loads(note_row['images']) if note_row['images'] else []
            except Exception:
                note_image_paths = []
            file_paths_to_delete.extend(path for path in note_image_paths if path)

        db.execute('DELETE FROM kanban_task_notes WHERE card_id = ?', (linked_card['id'],))
        db.execute('DELETE FROM tarefas WHERE id = ?', (linked_card['id'],))

    db.execute('DELETE FROM subpassos WHERE id = ?', (substep['id'],))
    db.execute(
        'UPDATE subpassos SET ordem = ordem - 1 WHERE step_id = ? AND ordem > ?',
        (step['id'], substep_index)
    )
    db.execute(
        '''
        UPDATE tarefas
        SET substep_index = substep_index - 1
        WHERE project_id = ? AND step_index = ? AND substep_index > ?
        ''',
        (project_id, step_index, substep_index)
    )

    remaining_substeps = db.execute(
        'SELECT id FROM subpassos WHERE step_id = ? ORDER BY ordem',
        (step['id'],)
    ).fetchall()
    ids_str = ','.join(str(row['id']) for row in remaining_substeps)
    db.execute('UPDATE passos SET subpassos_ids = ? WHERE id = ?', (ids_str, step['id']))

    db.commit()
    _invalidate_project_caches(project_id)
    return {
        'success': True,
        'status': 'deleted',
        'deleted_subtask_name': substep['nome'],
        'deleted_file_paths': file_paths_to_delete,
    }


def update_project_step_order(project_id, current_order, target_order, mode='insert'):
    db = get_db()
    ensure_project_status_columns(db)

    steps = db.execute(
        'SELECT id, ordem FROM passos WHERE project_id = ? ORDER BY ordem',
        (project_id,)
    ).fetchall()
    if not steps:
        return {'success': False, 'status': 'missing_step'}

    max_order = len(steps) - 1
    normalized_target = max(0, min(int(target_order), max_order))
    current_step = next((row for row in steps if int(row['ordem']) == int(current_order)), None)
    if current_step is None:
        return {'success': False, 'status': 'missing_step'}

    if int(current_order) == normalized_target:
        return {'success': True, 'status': 'unchanged', 'project': get_project(project_id)}

    normalized_mode = 'swap' if (mode or '').strip().lower() == 'swap' else 'insert'
    if normalized_mode == 'swap':
        target_step = next((row for row in steps if int(row['ordem']) == normalized_target), None)
        if target_step is None:
            return {'success': False, 'status': 'missing_target'}
        order_mapping = {
            int(current_order): normalized_target,
            normalized_target: int(current_order),
        }
    else:
        order_mapping = {}
        current_order_int = int(current_order)
        if normalized_target < current_order_int:
            for row in steps:
                old_order = int(row['ordem'])
                if normalized_target <= old_order < current_order_int:
                    order_mapping[old_order] = old_order + 1
            order_mapping[current_order_int] = normalized_target
        else:
            for row in steps:
                old_order = int(row['ordem'])
                if current_order_int < old_order <= normalized_target:
                    order_mapping[old_order] = old_order - 1
            order_mapping[current_order_int] = normalized_target

    if not order_mapping:
        return {'success': True, 'status': 'unchanged', 'project': get_project(project_id)}

    for old_order, new_order in order_mapping.items():
        db.execute(
            'UPDATE passos SET ordem = ? WHERE project_id = ? AND ordem = ?',
            (-(new_order + 1), project_id, old_order)
        )

    for old_order, new_order in order_mapping.items():
        db.execute(
            'UPDATE passos SET ordem = ? WHERE project_id = ? AND ordem = ?',
            (new_order, project_id, -(new_order + 1))
        )

    case_clauses = ' '.join('WHEN ? THEN ?' for _ in order_mapping)
    card_params = []
    for old_order, new_order in order_mapping.items():
        card_params.extend([old_order, new_order])
    affected_orders = tuple(order_mapping.keys())
    placeholders = ','.join('?' for _ in affected_orders)
    db.execute(
        f'''
        UPDATE tarefas
        SET step_index = CASE step_index
            {case_clauses}
            ELSE step_index
        END
        WHERE project_id = ? AND step_index IN ({placeholders})
        ''',
        tuple(card_params + [project_id, *affected_orders])
    )

    all_steps = db.execute(
        'SELECT id FROM passos WHERE project_id = ? ORDER BY ordem',
        (project_id,)
    ).fetchall()
    ids_str = ','.join(str(row['id']) for row in all_steps)
    db.execute('UPDATE projetos SET passos_ids = ? WHERE id = ?', (ids_str, project_id))

    db.commit()
    _invalidate_project_caches(project_id)
    return {'success': True, 'status': normalized_mode, 'project': get_project(project_id)}


def update_project_step_name(project_id, step_index, step_name):
    db = get_db()
    ensure_project_status_columns(db)
    clean_name = (step_name or '').strip()
    if not clean_name:
        return None

    step = db.execute(
        'SELECT id FROM passos WHERE project_id = ? AND ordem = ?',
        (project_id, step_index)
    ).fetchone()
    if not step:
        return None

    db.execute('UPDATE passos SET nome = ? WHERE id = ?', (clean_name, step['id']))
    _sync_linked_kanban_step_title(project_id, step_index, clean_name)
    db.commit()
    _invalidate_project_caches(project_id)
    return get_project(project_id)


def update_project_substep_name(project_id, step_index, substep_index, substep_name):
    db = get_db()
    ensure_project_status_columns(db)
    clean_name = (substep_name or '').strip()
    if not clean_name:
        return None

    step = db.execute(
        'SELECT id FROM passos WHERE project_id = ? AND ordem = ?',
        (project_id, step_index)
    ).fetchone()
    if not step:
        return None

    substep = db.execute(
        'SELECT id FROM subpassos WHERE step_id = ? AND ordem = ?',
        (step['id'], substep_index)
    ).fetchone()
    if not substep:
        return None

    db.execute('UPDATE subpassos SET nome = ? WHERE id = ?', (clean_name, substep['id']))
    _sync_linked_kanban_substep_title(project_id, step_index, substep_index, clean_name)
    db.commit()
    _invalidate_project_caches(project_id)
    return get_project(project_id)


def update_substep_details(project_id, step_index, substep_index, notes, links, new_images):
    db = get_db()
    step = db.execute('SELECT id FROM passos WHERE project_id = ? AND ordem = ?', (project_id, step_index)).fetchone()
    if step:
        substep = db.execute('SELECT id, images FROM subpassos WHERE step_id = ? AND ordem = ?', (step['id'], substep_index)).fetchone()
        if substep:
            try:
                current_images = json.loads(substep['images']) if substep['images'] else []
            except Exception:
                current_images = []

            updated_images = current_images + new_images

            db.execute('''
                UPDATE subpassos
                SET notes = ?, links = ?, images = ?
                WHERE id = ?
            ''', (notes, json.dumps(links), json.dumps(updated_images), substep['id']))
            db.commit()
            _invalidate_project_caches(project_id)
            return get_project(project_id)
    return None


def add_substep_kanban_note(project_id, step_index, substep_index, note_text, created_by_name='', image_paths=None):
    db = get_db()
    ensure_kanban_task_notes_table(db)

    clean_note = ' '.join((note_text or '').strip().split())
    normalized_image_paths = [path for path in (image_paths or []) if path]
    if not clean_note and not normalized_image_paths:
        return {'success': False, 'status': 'empty'}

    linked_card = db.execute(
        '''
        SELECT id
        FROM tarefas
        WHERE project_id = ? AND step_index = ? AND substep_index = ?
        LIMIT 1
        ''',
        (project_id, step_index, substep_index)
    ).fetchone()
    if not linked_card:
        return {'success': False, 'status': 'missing_card'}

    card_id = linked_card['id']
    notes_count = db.execute(
        'SELECT COUNT(*) AS total FROM kanban_task_notes WHERE card_id = ?',
        (card_id,)
    ).fetchone()['total']
    if notes_count >= 10:
        return {'success': False, 'status': 'limit_reached'}

    if clean_note:
        duplicate_note = db.execute(
            '''
            SELECT id
            FROM kanban_task_notes
            WHERE card_id = ? AND LOWER(TRIM(note)) = LOWER(TRIM(?))
            LIMIT 1
            ''',
            (card_id, clean_note)
        ).fetchone()
        if duplicate_note:
            return {'success': False, 'status': 'duplicate'}

    db.execute(
        '''
        INSERT INTO kanban_task_notes (card_id, note, images, created_by_name, created_at)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (
            card_id,
            clean_note,
            json.dumps(normalized_image_paths),
            (created_by_name or '').strip(),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
    )
    db.commit()
    _invalidate_project_caches(project_id)
    return {'success': True, 'status': 'created'}


def add_step_kanban_note(project_id, step_index, note_text, created_by_name='', image_paths=None):
    db = get_db()
    ensure_kanban_task_notes_table(db)

    clean_note = ' '.join((note_text or '').strip().split())
    normalized_image_paths = [path for path in (image_paths or []) if path]
    if not clean_note and not normalized_image_paths:
        return {'success': False, 'status': 'empty'}

    linked_card = db.execute(
        '''
        SELECT id
        FROM tarefas
        WHERE project_id = ? AND step_index = ? AND substep_index = ?
        LIMIT 1
        ''',
        (project_id, step_index, -1)
    ).fetchone()
    if not linked_card:
        return {'success': False, 'status': 'missing_card'}

    card_id = linked_card['id']
    notes_count = db.execute(
        'SELECT COUNT(*) AS total FROM kanban_task_notes WHERE card_id = ?',
        (card_id,)
    ).fetchone()['total']
    if notes_count >= 10:
        return {'success': False, 'status': 'limit_reached'}

    if clean_note:
        duplicate_note = db.execute(
            '''
            SELECT id
            FROM kanban_task_notes
            WHERE card_id = ? AND LOWER(TRIM(note)) = LOWER(TRIM(?))
            LIMIT 1
            ''',
            (card_id, clean_note)
        ).fetchone()
        if duplicate_note:
            return {'success': False, 'status': 'duplicate'}

    db.execute(
        '''
        INSERT INTO kanban_task_notes (card_id, note, images, created_by_name, created_at)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (
            card_id,
            clean_note,
            json.dumps(normalized_image_paths),
            (created_by_name or '').strip(),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
    )
    db.commit()
    _invalidate_project_caches(project_id)
    return {'success': True, 'status': 'created'}


def get_kanban_note_by_id(note_id):
    db = get_db()
    ensure_kanban_task_notes_table(db)
    row = db.execute(
        'SELECT id, card_id, note, images, created_by_name, created_at FROM kanban_task_notes WHERE id = ?',
        (int(note_id),)
    ).fetchone()
    if not row:
        return None
    note = dict(row)
    try:
        note['images'] = json.loads(note['images']) if note.get('images') else []
    except Exception:
        note['images'] = []
    return note


def _get_project_and_step_from_card_id(card_id):
    db = get_db()
    row = db.execute(
        'SELECT project_id, step_index FROM tarefas WHERE id = ? LIMIT 1',
        (int(card_id),)
    ).fetchone()
    if not row:
        return None, None
    return row['project_id'], row['step_index']


def update_step_kanban_note(note_id, note_text, new_image_paths=None, remove_image_paths=None, editor_name=''):
    db = get_db()
    ensure_kanban_task_notes_table(db)
    remove_image_paths = remove_image_paths or []
    new_image_paths = new_image_paths or []

    existing_note = get_kanban_note_by_id(note_id)
    if not existing_note:
        return {'success': False, 'status': 'missing'}

    clean_note = ' '.join((note_text or '').strip().split())
    current_images = list(existing_note.get('images') or [])
    remaining_images = [img for img in current_images if img not in remove_image_paths]
    updated_images = remaining_images + new_image_paths

    if not clean_note and not updated_images:
        return {'success': False, 'status': 'empty'}

    card_id = existing_note['card_id']
    if clean_note:
        duplicate_note = db.execute(
            '''
            SELECT id
            FROM kanban_task_notes
            WHERE card_id = ? AND LOWER(TRIM(note)) = LOWER(TRIM(?)) AND id != ?
            LIMIT 1
            ''',
            (card_id, clean_note, int(note_id))
        ).fetchone()
        if duplicate_note:
            return {'success': False, 'status': 'duplicate'}

    db.execute(
        '''
        UPDATE kanban_task_notes
        SET note = ?, images = ?
        WHERE id = ?
        ''',
        (
            clean_note,
            json.dumps(updated_images),
            int(note_id)
        )
    )
    db.commit()

    project_id, _ = _get_project_and_step_from_card_id(card_id)
    if project_id:
        _invalidate_project_caches(project_id)

    deleted_file_paths = [p for p in remove_image_paths if p in current_images]
    return {
        'success': True,
        'status': 'updated',
        'deleted_file_paths': deleted_file_paths
    }


def delete_step_kanban_note(note_id):
    db = get_db()
    ensure_kanban_task_notes_table(db)

    existing_note = get_kanban_note_by_id(note_id)
    if not existing_note:
        return {'success': False, 'status': 'missing'}

    card_id = existing_note['card_id']
    file_paths_to_delete = list(existing_note.get('images') or [])

    db.execute('DELETE FROM kanban_task_notes WHERE id = ?', (int(note_id),))
    db.commit()

    project_id, _ = _get_project_and_step_from_card_id(card_id)
    if project_id:
        _invalidate_project_caches(project_id)

    return {
        'success': True,
        'status': 'deleted',
        'deleted_file_paths': file_paths_to_delete,
        'note_creator': existing_note.get('created_by_name') or ''
    }


def add_project_substep(project_id, step_index, substep_name):
    db = get_db()
    ensure_project_status_columns(db)
    step = db.execute('SELECT id FROM passos WHERE project_id = ? AND ordem = ?', (project_id, step_index)).fetchone()
    if step:
        row = db.execute('SELECT MAX(ordem) as max_ord FROM subpassos WHERE step_id = ?', (step['id'],)).fetchone()
        next_ord = (row['max_ord'] + 1) if row['max_ord'] is not None else 0
        task_status_slug = _get_preferred_status_slug('todo')
        status_columns = {column['slug']: column for column in get_status_columns()}
        task_status_column = status_columns.get(task_status_slug)

        db.execute('''
            INSERT INTO subpassos (step_id, nome, status, status_coluna_id, ordem, notes, links, images)
            VALUES (?, ?, ?, ?, ?, '', '[]', '[]')
        ''', (step['id'], substep_name, task_status_slug, task_status_column['id'] if task_status_column else None, next_ord))

        # Update subpassos_ids
        all_subs = db.execute('SELECT id FROM subpassos WHERE step_id = ? ORDER BY ordem', (step['id'],)).fetchall()
        ids_str = ",".join([str(r['id']) for r in all_subs])
        db.execute('UPDATE passos SET subpassos_ids = ? WHERE id = ?', (ids_str, step['id']))

        db.commit()
        _invalidate_project_caches(project_id)
        return get_project(project_id)
    return None


def add_project_step(project_id, step_name):
    db = get_db()
    ensure_project_status_columns(db)
    clean_name = (step_name or '').strip()
    if not clean_name:
        return None

    row = db.execute(
        'SELECT MAX(ordem) as max_ord FROM passos WHERE project_id = ?',
        (project_id,)
    ).fetchone()
    next_ord = (row['max_ord'] + 1) if row['max_ord'] is not None else 0
    task_status_slug = _get_preferred_status_slug('todo')
    status_columns = {column['slug']: column for column in get_status_columns()}
    task_status_column = status_columns.get(task_status_slug)

    db.execute(
        'INSERT INTO passos (project_id, nome, status, status_coluna_id, ordem, subpassos_ids) VALUES (?, ?, ?, ?, ?, ?)',
        (project_id, clean_name, task_status_slug, task_status_column['id'] if task_status_column else None, next_ord, '')
    )

    all_steps = db.execute('SELECT id FROM passos WHERE project_id = ? ORDER BY ordem', (project_id,)).fetchall()
    ids_str = ",".join([str(r['id']) for r in all_steps])
    db.execute('UPDATE projetos SET passos_ids = ? WHERE id = ?', (ids_str, project_id))

    db.commit()
    _invalidate_project_caches(project_id)
    return get_project(project_id)


def create_project_step_kanban_card(project_id, step_index):
    db = get_db()
    ensure_project_status_columns(db)
    create_card = _get_kanban_card_helpers()
    _, _, get_default_column_slug, get_column_by_slug, _ = _get_kanban_helpers()

    existing_card = db.execute(
        'SELECT id FROM tarefas WHERE project_id = ? AND step_index = ? AND substep_index = ? LIMIT 1',
        (project_id, step_index, -1)
    ).fetchone()
    if existing_card:
        return str(existing_card['id'])

    project = get_project(project_id)
    if not project or step_index < 0 or step_index >= len(project['steps']):
        return None

    step = project['steps'][step_index]
    target_status_slug = _normalize_legacy_status(step.get('status'))
    target_column = get_column_by_slug(target_status_slug)
    create_card(
        title=step['name'],
        description='',
        column_id=target_column['slug'] if target_column else get_default_column_slug(),
        project_ref={'project_id': project_id, 'step_index': step_index, 'substep_index': -1}
    )
    created_card = db.execute(
        'SELECT id FROM tarefas WHERE project_id = ? AND step_index = ? AND substep_index = ? ORDER BY id DESC LIMIT 1',
        (project_id, step_index, -1)
    ).fetchone()
    _invalidate_project_caches(project_id)
    return str(created_card['id']) if created_card else None


def create_project_substep_kanban_card(project_id, step_index, substep_index):
    db = get_db()
    ensure_project_status_columns(db)
    create_card = _get_kanban_card_helpers()
    _, _, get_default_column_slug, get_column_by_slug, _ = _get_kanban_helpers()

    existing_card = db.execute(
        'SELECT id FROM tarefas WHERE project_id = ? AND step_index = ? AND substep_index = ? LIMIT 1',
        (project_id, step_index, substep_index)
    ).fetchone()
    if existing_card:
        return str(existing_card['id'])

    project = get_project(project_id)
    if not project or step_index < 0 or step_index >= len(project['steps']):
        return None

    step = project['steps'][step_index]
    if substep_index < 0 or substep_index >= len(step['substeps']):
        return None

    substep = step['substeps'][substep_index]
    target_status_slug = _normalize_legacy_status(substep.get('status'))
    target_column = get_column_by_slug(target_status_slug)
    create_card(
        title=substep['name'],
        description='',
        column_id=target_column['slug'] if target_column else get_default_column_slug(),
        project_ref={'project_id': project_id, 'step_index': step_index, 'substep_index': substep_index}
    )
    created_card = db.execute(
        'SELECT id FROM tarefas WHERE project_id = ? AND step_index = ? AND substep_index = ? ORDER BY id DESC LIMIT 1',
        (project_id, step_index, substep_index)
    ).fetchone()
    _invalidate_project_caches(project_id)
    return str(created_card['id']) if created_card else None
