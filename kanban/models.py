import json
import re
import sqlite3
from datetime import datetime
from flask import g, has_app_context
from werkzeug.security import generate_password_hash
try:
    from ..projects.models import load_projects, update_project_step, update_project_substep
    from ..db import get_db, is_runtime_schema_ready
    from ..runtime_cache import get_cached, set_cached, invalidate_cache, invalidate_cache_prefix
except (ImportError, ValueError):
    from projects.models import load_projects, update_project_step, update_project_substep
    try:
        from plm_qa_dashboard.db import get_db, is_runtime_schema_ready
    except ImportError:
        from db import get_db, is_runtime_schema_ready
    try:
        from runtime_cache import get_cached, set_cached, invalidate_cache, invalidate_cache_prefix  # type: ignore
    except ImportError:
        from runtime_cache import get_cached, set_cached, invalidate_cache, invalidate_cache_prefix  # type: ignore

# --- Migração leve de colunas de impedimento ---


def _get_request_cache():
    if not has_app_context():
        return None
    cache = getattr(g, '_kanban_model_cache', None)
    if cache is None:
        cache = {}
        g._kanban_model_cache = cache
    return cache


def _invalidate_request_cache(*keys):
    cache = _get_request_cache()
    if cache is None:
        return
    for key in keys:
        cache.pop(key, None)


def _invalidate_runtime_cache(*keys):
    invalidate_cache(*keys)


def _invalidate_kanban_caches(include_projects=False):
    _invalidate_request_cache('board_columns', 'sprints:1', 'sprints:0')
    _invalidate_runtime_cache(BOARD_COLUMNS_CACHE_KEY, SPRINTS_ALL_CACHE_KEY, SPRINTS_OPEN_CACHE_KEY)
    if include_projects:
        invalidate_cache('projects:all')
        invalidate_cache_prefix('project:')

def ensure_impedimento_columns(db):
    if is_runtime_schema_ready():
        return
    cursor = db.cursor()
    changed = False
    try:
        cursor.execute('ALTER TABLE tarefas ADD COLUMN impedido INTEGER DEFAULT 0')
        changed = True
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE tarefas ADD COLUMN impedimento TEXT')
        changed = True
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE tarefas ADD COLUMN data_entrega TEXT')
        changed = True
    except sqlite3.OperationalError:
        pass

    if changed:
        db.commit()


# --- Usuários ---

def get_users():
    db = get_db()
    cursor = db.execute('SELECT * FROM users ORDER BY name ASC')
    return [dict(row) for row in cursor.fetchall()]


def get_user_by_id(user_id):
    db = get_db()
    cursor = db.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    if row:
        return dict(row)
    return None


def create_user(name, password, key=None, color='#3b82f6', photo=None, is_admin=0):
    db = get_db()
    hashed_password = generate_password_hash(password)
    try:
        cursor = db.execute(
            'INSERT INTO users (name, password, key, color, photo, birthday, is_admin) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (name, hashed_password, key, color, photo, None, is_admin)
        )
        db.commit()
        # Return new user dict
        user = db.execute('SELECT * FROM users WHERE id = ?', (cursor.lastrowid,)).fetchone()
        return dict(user)
    except sqlite3.IntegrityError:
        return None


def update_user(user_id, name, password=None, key=None, color='#3b82f6', photo=None, is_admin=0):
    db = get_db()

    query = 'UPDATE users SET name = ?, key = ?, color = ?, is_admin = ?'
    params = [name, key, color, is_admin]

    if password:
        hashed_password = generate_password_hash(password)
        query += ', password = ?'
        params.append(hashed_password)

    if photo:
        query += ', photo = ?'
        params.append(photo)

    query += ' WHERE id = ?'
    params.append(user_id)

    try:
        db.execute(query, tuple(params))
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def delete_user(user_id):
    db = get_db()
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()


# --- Tarefas e Colunas ---

DEFAULT_COLUMNS = [
    {'slug': 'backlog', 'name': 'Backlog', 'order': 0, 'color': '#64748b'},
    {'slug': 'todo', 'name': 'A Fazer', 'order': 1, 'color': '#64748b'},
    {'slug': 'doing', 'name': 'Em Progresso', 'order': 2, 'color': '#64748b'},
    {'slug': 'done', 'name': 'Concluido', 'order': 3, 'color': '#64748b'}
]

DEFAULT_COLUMN_SLUG = DEFAULT_COLUMNS[0]['slug']
DONE_COLUMN_SLUG = 'done'
DEFAULT_COLUMN_COLOR = '#64748b'
BOARD_COLUMNS_CACHE_KEY = 'kanban:columns'
SPRINTS_ALL_CACHE_KEY = 'kanban:sprints:1'
SPRINTS_OPEN_CACHE_KEY = 'kanban:sprints:0'

SPRINT_STATUS_OPTIONS = {
    'planned': {
        'label': 'Planejada',
        'theme': {
            'bg': '#f3e8ff',
            'text': '#7c3aed',
            'border': '#d8b4fe'
        }
    },
    'active': {
        'label': 'Ativa',
        'theme': {
            'bg': '#dbeafe',
            'text': '#1d4ed8',
            'border': '#93c5fd'
        }
    },
    'completed': {
        'label': 'Concluida',
        'theme': {
            'bg': '#dcfce7',
            'text': '#166534',
            'border': '#86efac'
        }
    }
}
DEFAULT_SPRINT_STATUS = 'planned'


def _table_info(db, table_name):
    return {
        row['name']: dict(row)
        for row in db.execute(f'PRAGMA table_info({table_name})').fetchall()
    }


def _format_board_datetime(value):
    if not value:
        return ''

    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        pass

    for fmt in ('%Y-%m-%d %H:%M:%S', '%d/%m/%Y - %H:%M', '%d/%m/%Y %H:%M'):
        try:
            return datetime.strptime(value, fmt).strftime('%d/%m/%Y %H:%M')
        except ValueError:
            continue
    return value


def ensure_kanban_task_notes_table(db):
    if is_runtime_schema_ready():
        return
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS kanban_task_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            images TEXT NOT NULL DEFAULT '[]',
            created_by_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(card_id) REFERENCES tarefas(id) ON DELETE CASCADE
        )
        '''
    )

    notes_info = _table_info(db, 'kanban_task_notes')
    if 'images' not in notes_info:
        db.execute("ALTER TABLE kanban_task_notes ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
    if 'created_by_name' not in notes_info:
        db.execute("ALTER TABLE kanban_task_notes ADD COLUMN created_by_name TEXT NOT NULL DEFAULT ''")
    db.commit()


def get_sprint_status_options():
    return [
        {
            'value': value,
            'label': data['label'],
            'theme': dict(data['theme'])
        }
        for value, data in SPRINT_STATUS_OPTIONS.items()
    ]


def _normalize_sprint_status(status_value):
    clean_status = (status_value or '').strip().lower()
    if clean_status in SPRINT_STATUS_OPTIONS:
        return clean_status
    return DEFAULT_SPRINT_STATUS


def _parse_iso_date(value):
    clean_value = (value or '').strip()
    if not clean_value:
        return None
    try:
        return datetime.strptime(clean_value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _build_sprint_progress(sprint):
    status = _normalize_sprint_status(sprint.get('status'))
    today = datetime.now().date()
    start_date = _parse_iso_date(sprint.get('start_date'))
    end_date = _parse_iso_date(sprint.get('end_date'))

    progress = {
        'percent': 100 if status == 'completed' else 0,
        'phase': 'undefined',
        'label': 'Sem periodo definido',
        'bar_class': 'bg-gray-300',
        'hint': 'Defina data de inicio e fim para acompanhar o tempo da sprint.'
    }

    if start_date and end_date:
        total_days = max((end_date - start_date).days + 1, 1)
        elapsed_days = min(max((today - start_date).days + 1, 0), total_days)
        percent = int(round((elapsed_days / total_days) * 100))

        if today < start_date:
            progress.update({
                'percent': 0 if status != 'completed' else 100,
                'phase': 'upcoming',
                'label': 'Ainda nao iniciada',
                'bar_class': 'bg-violet-300',
                'hint': f'Comeca em {start_date.strftime("%d/%m/%Y")}.'
            })
        elif today > end_date:
            progress.update({
                'percent': 100,
                'phase': 'expired',
                'label': 'Prazo encerrado',
                'bar_class': 'bg-emerald-500' if status == 'completed' else 'bg-blue-500' if status == 'active' else 'bg-violet-300',
                'hint': f'Prazo final em {end_date.strftime("%d/%m/%Y")}.'
            })
        else:
            progress.update({
                'percent': 100 if status == 'completed' else percent,
                'phase': 'running',
                'label': 'Tempo em andamento',
                'bar_class': 'bg-emerald-500' if status == 'completed' else 'bg-blue-500' if status == 'active' else 'bg-violet-300',
                'hint': f'{elapsed_days} de {total_days} dias consumidos.'
            })

    if status == 'completed':
        progress.update({
            'percent': 100,
            'label': 'Concluida',
            'bar_class': 'bg-emerald-500',
            'hint': progress['hint'] if start_date and end_date else 'Sprint marcada como concluida.'
        })
    elif status == 'active' and progress['phase'] == 'upcoming':
        progress.update({
            'label': 'Ativa antes do inicio',
            'bar_class': 'bg-blue-500',
            'hint': f'Status ativo, mas a sprint inicia em {start_date.strftime("%d/%m/%Y")}.'
        })
    elif status == 'planned' and progress['phase'] == 'running':
        progress.update({
            'label': 'Planejada dentro do periodo',
            'bar_class': 'bg-violet-300',
            'hint': f'Periodo em curso, mas o status ainda esta como planejada.'
        })

    return progress


def _decorate_sprint(row):
    sprint = dict(row)
    sprint['id'] = int(sprint['id'])
    sprint['status'] = _normalize_sprint_status(sprint.get('status'))
    sprint['status_label'] = SPRINT_STATUS_OPTIONS[sprint['status']]['label']
    sprint['status_theme'] = dict(SPRINT_STATUS_OPTIONS[sprint['status']]['theme'])
    sprint['start_date_display'] = _format_board_datetime(sprint.get('start_date'))
    sprint['end_date_display'] = _format_board_datetime(sprint.get('end_date'))
    sprint['created_at_display'] = _format_board_datetime(sprint.get('created_at'))
    sprint['progress'] = _build_sprint_progress(sprint)
    return sprint


def ensure_kanban_sprints_table(db):
    if is_runtime_schema_ready():
        return
    changed = False
    db.execute(
        '''
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
        '''
    )
    sprint_info = _table_info(db, 'kanban_sprints')
    if 'description' not in sprint_info:
        db.execute('ALTER TABLE kanban_sprints ADD COLUMN description TEXT')
        changed = True
    if 'project_id' not in sprint_info:
        db.execute('ALTER TABLE kanban_sprints ADD COLUMN project_id TEXT')
        changed = True
    if 'status' not in sprint_info:
        db.execute(f"ALTER TABLE kanban_sprints ADD COLUMN status TEXT NOT NULL DEFAULT '{DEFAULT_SPRINT_STATUS}'")
        changed = True
    if 'start_date' not in sprint_info:
        db.execute('ALTER TABLE kanban_sprints ADD COLUMN start_date TEXT')
        changed = True
    if 'end_date' not in sprint_info:
        db.execute('ALTER TABLE kanban_sprints ADD COLUMN end_date TEXT')
        changed = True
    if 'created_by_name' not in sprint_info:
        db.execute("ALTER TABLE kanban_sprints ADD COLUMN created_by_name TEXT NOT NULL DEFAULT ''")
        changed = True

    tarefas_info = _table_info(db, 'tarefas')
    if 'sprint_id' not in tarefas_info:
        db.execute('ALTER TABLE tarefas ADD COLUMN sprint_id INTEGER')
        changed = True

    cursor = db.execute(
        '''
        UPDATE kanban_sprints
        SET status = ?
        WHERE status IS NULL
           OR TRIM(status) = ''
           OR status NOT IN ('planned', 'active', 'completed')
        ''',
        (DEFAULT_SPRINT_STATUS,)
    )
    if cursor.rowcount > 0:
        changed = True

    if changed:
        db.commit()


def get_sprints(include_completed=True):
    cache = _get_request_cache()
    cache_key = f'sprints:{1 if include_completed else 0}'
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    runtime_cache_key = SPRINTS_ALL_CACHE_KEY if include_completed else SPRINTS_OPEN_CACHE_KEY
    cached_sprints = get_cached(runtime_cache_key)
    if cached_sprints is not None:
        if cache is not None:
            cache[cache_key] = cached_sprints
        return cached_sprints
    db = get_db()
    ensure_kanban_sprints_table(db)

    query = '''
        SELECT id, name, description, project_id, status, start_date, end_date, created_by_name, created_at
        FROM kanban_sprints
    '''
    params = []
    if not include_completed:
        query += ' WHERE status != ?'
        params.append('completed')
    query += '''
        ORDER BY
            CASE status
                WHEN 'active' THEN 0
                WHEN 'planned' THEN 1
                ELSE 2
            END,
            CASE WHEN start_date IS NULL OR TRIM(start_date) = '' THEN 1 ELSE 0 END,
            start_date,
            id DESC
    '''
    rows = db.execute(query, tuple(params)).fetchall()
    sprints = [_decorate_sprint(row) for row in rows]
    if cache is not None:
        cache[cache_key] = sprints
    return set_cached(runtime_cache_key, sprints, ttl_seconds=5)


def get_sprint_by_id(sprint_id):
    if not sprint_id:
        return None
    for sprint in get_sprints():
        if int(sprint['id']) == int(sprint_id):
            return sprint
    return None


def create_sprint(name, description=None, project_id=None, status=DEFAULT_SPRINT_STATUS,
                  start_date=None, end_date=None, created_by_name=''):
    clean_name = (name or '').strip()
    if not clean_name:
        raise ValueError('O nome da sprint e obrigatorio.')

    clean_status = _normalize_sprint_status(status)
    clean_start = (start_date or '').strip() or None
    clean_end = (end_date or '').strip() or None
    if clean_start and clean_end and clean_start > clean_end:
        raise ValueError('A data inicial nao pode ser maior que a data final.')

    db = get_db()
    cursor = db.execute(
        '''
        INSERT INTO kanban_sprints (
            name, description, project_id, status, start_date, end_date, created_by_name, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            clean_name,
            (description or '').strip() or None,
            (project_id or '').strip() or None,
            clean_status,
            clean_start,
            clean_end,
            (created_by_name or '').strip(),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
    )
    db.commit()
    _invalidate_kanban_caches()
    return get_sprint_by_id(cursor.lastrowid)


def update_sprint(sprint_id, name, description=None, project_id=None, status=DEFAULT_SPRINT_STATUS,
                  start_date=None, end_date=None):
    clean_name = (name or '').strip()
    if not clean_name:
        raise ValueError('O nome da sprint e obrigatorio.')

    clean_status = _normalize_sprint_status(status)
    clean_start = (start_date or '').strip() or None
    clean_end = (end_date or '').strip() or None
    if clean_start and clean_end and clean_start > clean_end:
        raise ValueError('A data inicial nao pode ser maior que a data final.')

    db = get_db()
    cursor = db.execute(
        '''
        UPDATE kanban_sprints
        SET name = ?, description = ?, project_id = ?, status = ?, start_date = ?, end_date = ?
        WHERE id = ?
        ''',
        (
            clean_name,
            (description or '').strip() or None,
            (project_id or '').strip() or None,
            clean_status,
            clean_start,
            clean_end,
            sprint_id
        )
    )
    db.commit()
    _invalidate_kanban_caches()
    if cursor.rowcount <= 0:
        raise ValueError('Sprint nao encontrada.')
    return get_sprint_by_id(sprint_id)


def delete_sprint(sprint_id):
    db = get_db()
    db.execute('UPDATE tarefas SET sprint_id = NULL WHERE sprint_id = ?', (sprint_id,))
    cursor = db.execute('DELETE FROM kanban_sprints WHERE id = ?', (sprint_id,))
    db.commit()
    _invalidate_kanban_caches()
    if cursor.rowcount <= 0:
        raise ValueError('Sprint nao encontrada.')
    return True


def _normalize_hex_color(color_value):
    color = str(color_value or '').strip().lower()
    if re.fullmatch(r'#[0-9a-f]{6}', color):
        return color
    if re.fullmatch(r'#[0-9a-f]{3}', color):
        return '#' + ''.join(ch * 2 for ch in color[1:])
    return DEFAULT_COLUMN_COLOR


def _hex_to_rgb(color_value):
    color = _normalize_hex_color(color_value)
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _rgb_to_hex(rgb):
    r, g, b = [max(0, min(255, int(value))) for value in rgb]
    return f'#{r:02x}{g:02x}{b:02x}'


def _mix_colors(base_color, target_color, ratio):
    base_rgb = _hex_to_rgb(base_color)
    target_rgb = _hex_to_rgb(target_color)
    mixed = tuple(
        round(base_rgb[index] * (1 - ratio) + target_rgb[index] * ratio)
        for index in range(3)
    )
    return _rgb_to_hex(mixed)


def _get_contrast_text(color_value):
    r, g, b = _hex_to_rgb(color_value)
    luminance = (0.299 * r) + (0.587 * g) + (0.114 * b)
    return '#111827' if luminance > 186 else '#ffffff'


def get_column_theme(color_value):
    accent = _normalize_hex_color(color_value)
    text = _mix_colors(accent, '#111827', 0.45)
    return {
        'accent': accent,
        'accent_text': _get_contrast_text(accent),
        'surface': _mix_colors(accent, '#ffffff', 0.9),
        'surface_strong': _mix_colors(accent, '#ffffff', 0.78),
        'border': _mix_colors(accent, '#ffffff', 0.6),
        'text': text,
        'pill_bg': _mix_colors(accent, '#ffffff', 0.78),
        'pill_text': text
    }


def _decorate_column(column):
    decorated_column = dict(column)
    decorated_column['color'] = _normalize_hex_color(decorated_column.get('color'))
    decorated_column['theme'] = get_column_theme(decorated_column['color'])
    return decorated_column


def _generate_column_slug(name, existing_slugs):
    base_slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    if not base_slug:
        base_slug = 'coluna'

    candidate = base_slug
    suffix = 2
    while candidate in existing_slugs:
        candidate = f'{base_slug}-{suffix}'
        suffix += 1
    return candidate


def _create_kanban_columns_table(db, table_name='kanban_colunas'):
    db.execute(f'''
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            nome TEXT NOT NULL,
            ordem INTEGER NOT NULL,
            cor TEXT NOT NULL DEFAULT '{DEFAULT_COLUMN_COLOR}'
        )
    ''')


def _ensure_tarefas_column_reference(db):
    tarefas_info = _table_info(db, 'tarefas')
    changed = False

    if 'coluna_id' not in tarefas_info:
        db.execute('ALTER TABLE tarefas ADD COLUMN coluna_id INTEGER')
        changed = True

    cursor = db.execute('''
        UPDATE tarefas
        SET coluna_id = (
            SELECT kc.id
            FROM kanban_colunas kc
            WHERE kc.slug = tarefas.coluna
        )
        WHERE coluna_id IS NULL AND coluna IS NOT NULL
    ''')
    if cursor.rowcount > 0:
        changed = True

    default_column = db.execute(
        'SELECT id, slug FROM kanban_colunas ORDER BY ordem, id LIMIT 1'
    ).fetchone()
    if default_column:
        cursor = db.execute(
            'UPDATE tarefas SET coluna_id = ? WHERE coluna_id IS NULL',
            (default_column['id'],)
        )
        if cursor.rowcount > 0:
            changed = True

        cursor = db.execute('''
            UPDATE tarefas
            SET coluna = (
                SELECT kc.slug
                FROM kanban_colunas kc
                WHERE kc.id = tarefas.coluna_id
            )
            WHERE coluna_id IS NOT NULL
        ''')
        if cursor.rowcount > 0:
            changed = True

    if changed:
        db.commit()


def ensure_kanban_columns_table(db):
    if is_runtime_schema_ready():
        return
    changed = False
    kanban_info = _table_info(db, 'kanban_colunas')

    if not kanban_info:
        _create_kanban_columns_table(db)
        changed = True
    elif 'slug' not in kanban_info or kanban_info['id']['type'].upper() != 'INTEGER':
        rows = db.execute('SELECT id, nome, ordem FROM kanban_colunas ORDER BY ordem, id').fetchall()
        _create_kanban_columns_table(db, 'kanban_colunas_nova')

        used_slugs = set()
        for index, row in enumerate(rows):
            original_slug = str(row['id'] or '').strip()
            slug = original_slug or _generate_column_slug(row['nome'], used_slugs)
            if slug in used_slugs:
                slug = _generate_column_slug(row['nome'], used_slugs)
            used_slugs.add(slug)
            db.execute(
                'INSERT INTO kanban_colunas_nova (slug, nome, ordem, cor) VALUES (?, ?, ?, ?)',
                (slug, row['nome'], row['ordem'] if row['ordem'] is not None else index, DEFAULT_COLUMN_COLOR)
            )

        db.execute('DROP TABLE kanban_colunas')
        db.execute('ALTER TABLE kanban_colunas_nova RENAME TO kanban_colunas')
        changed = True

    kanban_info = _table_info(db, 'kanban_colunas')
    if 'cor' not in kanban_info:
        db.execute(f"ALTER TABLE kanban_colunas ADD COLUMN cor TEXT NOT NULL DEFAULT '{DEFAULT_COLUMN_COLOR}'")
        changed = True

    cursor = db.execute(
        'UPDATE kanban_colunas SET cor = ? WHERE cor IS NULL OR TRIM(cor) = ""',
        (DEFAULT_COLUMN_COLOR,)
    )
    if cursor.rowcount > 0:
        changed = True

    existing_count = db.execute('SELECT COUNT(*) AS total FROM kanban_colunas').fetchone()['total']
    if not existing_count:
        for index, column in enumerate(DEFAULT_COLUMNS):
            db.execute(
                'INSERT INTO kanban_colunas (slug, nome, ordem, cor) VALUES (?, ?, ?, ?)',
                (column['slug'], column['name'], index, column['color'])
            )
        changed = True

    _ensure_tarefas_column_reference(db)

    if changed:
        db.commit()


def get_board_columns():
    cache = _get_request_cache()
    if cache is not None and 'board_columns' in cache:
        return cache['board_columns']
    cached_columns = get_cached(BOARD_COLUMNS_CACHE_KEY)
    if cached_columns is not None:
        if cache is not None:
            cache['board_columns'] = cached_columns
        return cached_columns
    db = get_db()
    ensure_kanban_columns_table(db)
    rows = db.execute('SELECT id, slug, nome, ordem, cor FROM kanban_colunas ORDER BY ordem, id').fetchall()
    columns = [
        _decorate_column({'id': row['id'], 'slug': row['slug'], 'name': row['nome'], 'order': row['ordem'], 'color': row['cor']})
        for row in rows
    ]
    if cache is not None:
        cache['board_columns'] = columns
    return set_cached(BOARD_COLUMNS_CACHE_KEY, columns, ttl_seconds=5)


def get_default_column_slug():
    columns = get_board_columns()
    if not columns:
        return DEFAULT_COLUMN_SLUG
    return columns[0]['slug']


def get_default_column_id():
    return get_default_column_slug()


def get_column_by_slug(column_slug):
    if not column_slug:
        return None
    for column in get_board_columns():
        if column['slug'] == column_slug:
            return dict(column)
    return None


def create_column(name, color=None):
    clean_name = (name or '').strip()
    if not clean_name:
        raise ValueError('O nome da coluna e obrigatorio.')
    clean_color = _normalize_hex_color(color)

    db = get_db()
    columns = get_board_columns()
    existing_slugs = {column['slug'] for column in columns}
    new_slug = _generate_column_slug(clean_name, existing_slugs)
    done_index = next((index for index, column in enumerate(columns) if column['slug'] == DONE_COLUMN_SLUG), len(columns))

    db.execute('UPDATE kanban_colunas SET ordem = ordem + 1 WHERE ordem >= ?', (done_index,))
    cursor = db.execute(
        'INSERT INTO kanban_colunas (slug, nome, ordem, cor) VALUES (?, ?, ?, ?)',
        (new_slug, clean_name, done_index, clean_color)
    )
    db.commit()
    _invalidate_kanban_caches(include_projects=True)

    return _decorate_column({'id': cursor.lastrowid, 'slug': new_slug, 'name': clean_name, 'order': done_index, 'color': clean_color})


def update_column(column_slug, name, color=None):
    clean_name = (name or '').strip()
    if not clean_name:
        raise ValueError('O nome da coluna e obrigatorio.')
    clean_color = _normalize_hex_color(color)

    db = get_db()
    cursor = db.execute(
        'UPDATE kanban_colunas SET nome = ?, cor = ? WHERE slug = ?',
        (clean_name, clean_color, column_slug)
    )
    db.commit()
    _invalidate_kanban_caches(include_projects=True)

    if cursor.rowcount <= 0:
        raise ValueError('Coluna nao encontrada.')

    return True


def reorder_columns(column_slugs):
    db = get_db()
    columns = get_board_columns()
    column_map = {column['slug']: dict(column) for column in columns}
    current_slugs = set(column_map.keys())
    requested_slugs = [column_slug for column_slug in column_slugs if column_slug in current_slugs]

    if set(requested_slugs) != current_slugs:
        raise ValueError('A nova ordem das colunas esta invalida.')

    for index, column_slug in enumerate(requested_slugs):
        db.execute(
            'UPDATE kanban_colunas SET ordem = ? WHERE slug = ?',
            (index, column_slug)
        )
    db.commit()
    _invalidate_kanban_caches(include_projects=True)

    return [column_map[column_slug] for column_slug in requested_slugs]


def delete_column(column_slug, fallback_column_slug=None):
    db = get_db()
    columns = get_board_columns()
    if len(columns) <= 1:
        raise ValueError('O quadro precisa ter ao menos uma coluna.')

    target_column = next((column for column in columns if column['slug'] == column_slug), None)
    if not target_column:
        raise ValueError('Coluna nao encontrada.')

    remaining_columns = [column for column in columns if column['slug'] != column_slug]
    if not remaining_columns:
        raise ValueError('O quadro precisa ter ao menos uma coluna.')

    fallback_column = next((column for column in remaining_columns if column['slug'] == fallback_column_slug), None)
    if not fallback_column:
        fallback_column = remaining_columns[0]

    db.execute(
        'UPDATE tarefas SET coluna_id = ?, coluna = ? WHERE coluna_id = ? OR coluna = ?',
        (fallback_column['id'], fallback_column['slug'], target_column['id'], target_column['slug'])
    )
    db.execute('DELETE FROM kanban_colunas WHERE id = ?', (target_column['id'],))

    for index, column in enumerate(remaining_columns):
        db.execute('UPDATE kanban_colunas SET ordem = ? WHERE id = ?', (index, column['id']))
    db.commit()
    _invalidate_kanban_caches(include_projects=True)
    return True


def get_board_data(archived=False, sprints=None):
    columns = get_board_columns()

    # Load cards from DB
    db = get_db()
    sprints = sprints if sprints is not None else get_sprints()
    sprints_by_id = {int(sprint['id']): sprint for sprint in sprints}
    note_rows = db.execute(
        '''
        SELECT card_id, note, images, created_by_name, created_at
        FROM kanban_task_notes
        ORDER BY datetime(created_at) DESC, id DESC
        '''
    ).fetchall()
    notes_by_card = {}
    for note_row in note_rows:
        images = []
        try:
            images = json.loads(note_row['images']) if note_row['images'] else []
        except (TypeError, ValueError):
            images = []

        notes_by_card.setdefault(str(note_row['card_id']), []).append({
            'note': note_row['note'],
            'images': images,
            'created_by_name': note_row['created_by_name'],
            'created_at': note_row['created_at'],
            'created_at_display': _format_board_datetime(note_row['created_at'])
        })

    query = 'SELECT * FROM tarefas WHERE arquivado = ?'
    params = (1 if archived else 0,)

    cursor = db.execute(query, params)
    rows = cursor.fetchall()

    cards = []
    valid_column_slugs = {column['slug'] for column in columns}
    default_column_slug = columns[0]['slug'] if columns else DEFAULT_COLUMN_SLUG
    for row in rows:
        column_slug = row['coluna'] or default_column_slug
        if column_slug not in valid_column_slugs:
            column_slug = default_column_slug

        card = {
            'id': str(row['id']),
            'title': row['nome_tarefa'],
            'description': row['descricao'],
            'column_id': column_slug,
            'assigned_to': str(row['responsavel']) if row['responsavel'] else None,
            'sprint_id': int(row['sprint_id']) if 'sprint_id' in row.keys() and row['sprint_id'] else None,
            'priority': row['prioridade'],
            'start_date': row['data_inicio'],
            'end_date': row['data_fim'],
            'impedido': bool(row['impedido']) if 'impedido' in row.keys() else False,
            'impedimento': row['impedimento'] if 'impedimento' in row.keys() else None,
            'delivery_date': row['data_entrega'] if 'data_entrega' in row.keys() else None,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'notes': notes_by_card.get(str(row['id']), []),
            'notes_count': len(notes_by_card.get(str(row['id']), []))
        }
        card['sprint'] = sprints_by_id.get(card['sprint_id']) if card['sprint_id'] else None

        # Reconstruct project_ref
        if row['project_id']:
            card['project_ref'] = {
                'project_id': row['project_id'],
                'step_index': row['step_index'],
                'substep_index': row['substep_index']
            }
        else:
            card['project_ref'] = None

        cards.append(card)

    return {
        'columns': columns,
        'cards': cards
    }


def add_card_note(card_id, note_text, created_by_name='', image_paths=None):
    db = get_db()

    clean_note = ' '.join((note_text or '').strip().split())
    normalized_image_paths = [path for path in (image_paths or []) if path]
    if not clean_note and not normalized_image_paths:
        return {'success': False, 'status': 'empty'}

    card = db.execute('SELECT id FROM tarefas WHERE id = ? LIMIT 1', (card_id,)).fetchone()
    if not card:
        return {'success': False, 'status': 'missing_card'}

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
    _invalidate_kanban_caches(include_projects=True)
    return {'success': True, 'status': 'created'}


def create_card(title, description, column_id=DEFAULT_COLUMN_SLUG, assigned_to=None, project_ref=None,
                priority='Media', start_date=None, end_date=None, impedido=0, impedimento=None, sprint_id=None):
    db = get_db()

    target_column = get_column_by_slug(column_id)
    if not target_column:
        target_column = get_column_by_slug(get_default_column_slug())

    project_id = None
    step_index = None
    substep_index = None
    normalized_assigned_to = None
    normalized_sprint_id = None

    if assigned_to not in (None, ''):
        normalized_assigned_to = int(assigned_to)
        assigned_user = get_user_by_id(normalized_assigned_to)
        if not assigned_user:
            raise ValueError('O responsável selecionado não é válido.')

    if sprint_id not in (None, ''):
        normalized_sprint_id = int(sprint_id)
        if not get_sprint_by_id(normalized_sprint_id):
            raise ValueError('A sprint selecionada não é válida.')

    if project_ref:
        project_id = project_ref.get('project_id')
        step_index = project_ref.get('step_index')
        substep_index = project_ref.get('substep_index')

        existing_linked_card = db.execute(
            '''
            SELECT id
            FROM tarefas
            WHERE project_id = ? AND step_index = ? AND substep_index = ?
            LIMIT 1
            ''',
            (project_id, step_index, substep_index)
        ).fetchone()
        if existing_linked_card:
            raise ValueError('Ja existe um card do Kanban vinculado a esta tarefa/subtarefa do projeto.')

    cursor = db.execute('''
        INSERT INTO tarefas (
            nome_tarefa, descricao, coluna, coluna_id, responsavel, prioridade, data_inicio, data_fim,
            arquivado, project_id, step_index, substep_index, impedido, impedimento, sprint_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
    ''', (title, description, target_column['slug'], target_column['id'], normalized_assigned_to, priority, start_date, end_date,
          project_id, step_index, substep_index, impedido, impedimento, normalized_sprint_id))
    db.commit()
    _invalidate_kanban_caches(include_projects=True)

    card_id = cursor.lastrowid
    sprint = get_sprint_by_id(normalized_sprint_id) if normalized_sprint_id else None

    # Return new card dict structure
    new_card = {
        'id': str(card_id),
        'title': title,
        'description': description,
        'column_id': target_column['slug'],
        'assigned_to': str(normalized_assigned_to) if normalized_assigned_to else None,
        'project_ref': project_ref,
        'sprint_id': normalized_sprint_id,
        'sprint': sprint,
        'priority': priority,
        'start_date': start_date,
        'end_date': end_date,
        'impedido': bool(impedido),
        'impedimento': impedimento
    }
    return new_card


def update_card_position(card_id, new_column_id):
    db = get_db()
    target_column = get_column_by_slug(new_column_id)
    if not target_column:
        return False

    if new_column_id == DONE_COLUMN_SLUG:
        today = datetime.now().strftime('%Y-%m-%d')
        cursor = db.execute(
            'UPDATE tarefas SET coluna = ?, coluna_id = ?, data_entrega = ? WHERE id = ?',
            (target_column['slug'], target_column['id'], today, card_id)
        )
    else:
        # Se mover para fora de concluído, removemos a data de entrega?
        # O pedido diz "Sempre que uma tarefa for para a coluna concluído deve ser adicionada a data de entrega".
        # E "se eles tiverem sido concluidos, mostre a data".
        # Para evitar mostrar data de entrega em card que voltou para Doing, vamos limpar.
        cursor = db.execute(
            'UPDATE tarefas SET coluna = ?, coluna_id = ?, data_entrega = NULL WHERE id = ?',
            (target_column['slug'], target_column['id'], card_id)
        )

    db.commit()

    if cursor.rowcount > 0:
        _invalidate_kanban_caches(include_projects=True)
        # Fetch card to check project sync
        card = db.execute('SELECT * FROM tarefas WHERE id = ?', (card_id,)).fetchone()
        if card and card['project_id']:
            if card['substep_index'] is not None and int(card['substep_index']) == -1:
                update_project_step(card['project_id'], card['step_index'], new_column_id, sync_kanban=False)
            elif card['substep_index'] is not None:
                update_project_substep(card['project_id'], card['step_index'], card['substep_index'], new_column_id, sync_kanban=False)
        return True
    return False


def update_card_details(card_id, title, description, assigned_to, priority, start_date, end_date,
                        impedido=0, impedimento=None, sprint_id=None):
    db = get_db()
    db.execute('''
        UPDATE tarefas
        SET nome_tarefa = ?, descricao = ?, responsavel = ?, prioridade = ?, data_inicio = ?, data_fim = ?,
            impedido = ?, impedimento = ?, sprint_id = ?
        WHERE id = ?
    ''', (title, description, assigned_to, priority, start_date, end_date, impedido, impedimento, sprint_id or None, card_id))
    db.commit()
    _invalidate_kanban_caches(include_projects=True)
    return True


def delete_card(card_id):
    db = get_db()
    db.execute('DELETE FROM tarefas WHERE id = ?', (card_id,))
    db.commit()
    _invalidate_kanban_caches(include_projects=True)


def archive_card(card_id):
    db = get_db()
    db.execute('UPDATE tarefas SET arquivado = 1 WHERE id = ?', (card_id,))
    db.commit()
    _invalidate_kanban_caches(include_projects=True)


def unarchive_card(card_id):
    db = get_db()
    db.execute('UPDATE tarefas SET arquivado = 0 WHERE id = ?', (card_id,))
    db.commit()
    _invalidate_kanban_caches(include_projects=True)


def get_card_by_id(card_id):
    db = get_db()
    cursor = db.execute('SELECT * FROM tarefas WHERE id = ?', (card_id,))
    row = cursor.fetchone()
    if row:
        return dict(row)
    return None


def get_linked_project_task_refs():
    db = get_db()
    rows = db.execute(
        '''
        SELECT project_id, step_index, substep_index
        FROM tarefas
        WHERE project_id IS NOT NULL AND step_index IS NOT NULL AND substep_index IS NOT NULL
        '''
    ).fetchall()

    refs = set()
    for row in rows:
        refs.add((str(row['project_id']), int(row['step_index']), int(row['substep_index'])))
    return refs


def get_project_tasks_available(projects=None, linked_refs=None):
    try:
        projects = projects if projects is not None else load_projects()
        linked_refs = linked_refs if linked_refs is not None else get_linked_project_task_refs()
        tasks = []
        for p in projects:
            if p.get('project_status') == 'completed':
                continue
            for i, step in enumerate(p.get('steps', [])):
                if step.get('status') != DONE_COLUMN_SLUG and (str(p['id']), i, -1) not in linked_refs:
                    tasks.append({
                        'project_id': p['id'],
                        'project_name': p['name'],
                        'step_index': i,
                        'substep_index': -1,  # Simplificacao
                        'name': step['name']
                    })
        return tasks
    except Exception:
        return []


def get_all_projects():
    try:
        return load_projects()
    except Exception:
        return []
