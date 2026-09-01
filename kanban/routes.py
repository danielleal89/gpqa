import sqlite3

from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash
from flask_login import login_required, current_user
from datetime import datetime, date
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from werkzeug.utils import secure_filename
try:
    from ..storage import save_upload
except (ImportError, ValueError):
    from storage import save_upload  # type: ignore
from .models import (
    get_board_data, create_card, update_card_position, reorder_column_cards, create_column, update_column, reorder_columns, delete_column,
    update_card_details, delete_card, archive_card, unarchive_card, get_users, add_card_note, get_card_by_id,
    get_project_tasks_available, get_all_projects, get_default_column_id, get_default_column_slug,
    get_linked_project_task_refs, get_sprints, create_sprint, update_sprint, delete_sprint,
    get_sprint_status_options, get_sprint_by_id, get_board_columns, DONE_COLUMN_SLUG
)
try:
    from ..projects.models import get_project, update_project_substep
except (ImportError, ValueError):
    from projects.models import get_project, update_project_substep  # type: ignore

kanban_bp = Blueprint('kanban', __name__, template_folder='../pages/kanban')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _can_edit_card(card):
    if not getattr(current_user, 'is_authenticated', False):
        return False
    if getattr(current_user, 'is_admin', False):
        return True
    return str(card.get('responsavel') or '') == str(getattr(current_user, 'id', ''))


def _can_toggle_project_subtask(substep, parent_card=None):
    if not getattr(current_user, 'is_authenticated', False):
        return False
    if getattr(current_user, 'is_admin', False):
        return True

    kanban_card = (substep or {}).get('kanban_card') or {}
    assigned_to_id = str(kanban_card.get('assigned_to_id') or '')
    if not assigned_to_id and parent_card:
        assigned_to_id = str(parent_card.get('responsavel') or parent_card.get('assigned_to') or '')
    return assigned_to_id == str(getattr(current_user, 'id', ''))


def _redirect_back_with_note(card_id, status):
    fallback_url = url_for('kanban.board')
    referrer = request.referrer or fallback_url
    parsed = urlparse(referrer)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query['open_notes_card'] = str(card_id)
    query['note_status'] = status
    return redirect(urlunparse(parsed._replace(query=urlencode(query))))


def _render_board(archived_view=False):
    selected_user_id = request.args.get('user_id') or ''
    selected_project_id = request.args.get('project_id') or ''
    selected_sprint_id = request.args.get('sprint_id') or ''
    view_mode = request.args.get('view') or 'board'

    sprints = get_sprints()
    data = get_board_data(archived=archived_view, sprints=sprints)
    users = get_users()
    all_projects = get_all_projects()
    linked_project_refs = get_linked_project_task_refs()
    project_tasks = get_project_tasks_available(all_projects, linked_project_refs)

    # Create project map {id: name}
    projects_dict = {p['id']: p for p in all_projects}

    for project in all_projects:
        project_has_available_items = False
        for step_index, step in enumerate(project.get('steps', [])):
            step['kanban_step_index'] = step_index
            step['kanban_linked'] = (str(project['id']), step_index, -1) in linked_project_refs
            step['show_in_kanban_selector'] = not step['kanban_linked']
            if step['show_in_kanban_selector']:
                project_has_available_items = True

        project['show_in_kanban_selector'] = project_has_available_items

    today_str = datetime.now().strftime('%Y-%m-%d')

    # Organizar cards por coluna
    columns_data = {}
    for col in data['columns']:
        columns_data[col['slug']] = {
            'id': col['id'],
            'slug': col['slug'],
            'name': col['name'],
            'theme': col.get('theme', {}),
            'cards': []
        }

    for card in data['cards']:
        if card.get('project_ref') and int(card['project_ref'].get('substep_index', -1)) != -1:
            continue

        # Filtrar por usuário, se selecionado
        if selected_user_id and str(card.get('assigned_to') or '') != selected_user_id:
            continue

        card_project_id = ''
        if card.get('project_ref'):
            card_project_id = str(card['project_ref'].get('project_id') or '')
        if selected_project_id == '__no_project__':
            if card_project_id:
                continue
        elif selected_project_id and card_project_id != selected_project_id:
            continue

        card_sprint_id = str(card.get('sprint_id') or '')
        if selected_sprint_id == '__no_sprint__':
            if card_sprint_id:
                continue
        elif selected_sprint_id and card_sprint_id != selected_sprint_id:
            continue

        if card['column_id'] in columns_data:
            # Enriquecer card com info do usuário
            assigned_user = next((u for u in users if str(u['id']) == str(card.get('assigned_to') or '')), None)
            card['user_obj'] = assigned_user
            card['subtasks'] = []
            card['can_toggle_subtasks'] = False

            # Enriquecer com nome do projeto, tarefa e subtarefas
            if card.get('project_ref'):
                pid = card['project_ref'].get('project_id')
                step_idx = card['project_ref'].get('step_index')
                substep_idx = card['project_ref'].get('substep_index')

                project = projects_dict.get(pid)
                if project:
                    card['project_name'] = project['name']

                    if step_idx is not None and 0 <= int(step_idx) < len(project.get('steps', [])):
                        step = project['steps'][int(step_idx)]
                        card['task_name'] = step['name']
                        if substep_idx is not None and int(substep_idx) == -1:
                            card['subtasks'] = [
                                {
                                    'substep_index': substep.get('ordem', sub_index),
                                    'name': substep.get('name'),
                                    'is_done': substep.get('status') == DONE_COLUMN_SLUG,
                                    'completed_at': substep.get('completed_at') or '',
                                    'can_toggle': _can_toggle_project_subtask(substep, parent_card=card),
                                }
                                for sub_index, substep in enumerate(step.get('substeps', []))
                            ]
                            card['can_toggle_subtasks'] = any(
                                bool(subtask.get('can_toggle'))
                                for subtask in card['subtasks']
                            )
                    else:
                        card['task_name'] = None
                else:
                    card['project_name'] = 'Desconhecido'
                    card['task_name'] = None

            if card.get('sprint') and card['sprint'].get('project_id'):
                sprint_project = projects_dict.get(card['sprint']['project_id'])
                if sprint_project:
                    card['sprint']['project_name'] = sprint_project['name']

            # Check overdue
            if card.get('end_date') and card['end_date'] < today_str and card['column_id'] != 'done':
                card['is_overdue'] = True
            else:
                card['is_overdue'] = False

            columns_data[card['column_id']]['cards'].append(card)

    vacation_countdown = None
    if getattr(current_user, 'name', None) == 'Andréia':
        vacation_start = date(2026, 10, 13)
        days_remaining = (vacation_start - datetime.now().date()).days
        vacation_countdown = {
            'days_remaining': days_remaining,
            'start_date_display': '13/10/2026'
        }

    return render_template(
        'board.html',
        columns=columns_data,
        users=users,
        project_tasks=project_tasks,
        all_projects=all_projects,
        sprints=sprints,
        sprint_status_options=get_sprint_status_options(),
        first_column_id=get_default_column_id(),
        selected_user_id=selected_user_id,
        selected_project_id=selected_project_id,
        selected_sprint_id=selected_sprint_id,
        view_mode=view_mode,
        archived_view=archived_view,
        vacation_countdown=vacation_countdown
    )


@kanban_bp.route('/')
@login_required
def board():
    if request.args.get('archived') == '1':
        query = dict(parse_qsl(urlparse(request.url).query, keep_blank_values=True))
        query.pop('archived', None)
        return redirect(url_for('kanban.archived_board', **query))
    return _render_board(archived_view=False)


@kanban_bp.route('/arquivados')
@login_required
def archived_board():
    return _render_board(archived_view=True)


def _get_all_cards_including_archived(sprints=None):
    active_cards = get_board_data(archived=False, sprints=sprints)['cards']
    archived_cards = get_board_data(archived=True, sprints=sprints)['cards']
    return active_cards + archived_cards


def _sprint_task_counts(cards):
    total = len(cards)
    done = sum(1 for card in cards if card['column_id'] == DONE_COLUMN_SLUG)
    percent = int(round(done / total * 100)) if total else 0
    return {'total': total, 'done': done, 'percent': percent}


def _is_card_overdue(card, today):
    if card['column_id'] == DONE_COLUMN_SLUG:
        return False
    end_date = (card.get('end_date') or '').strip()
    if not end_date:
        return False
    try:
        return datetime.strptime(end_date, '%Y-%m-%d').date() < today
    except ValueError:
        return False


def _build_sprint_dashboard(cards, users, columns):
    users_by_id = {str(user['id']): user for user in users}
    columns_by_slug = {column['slug']: column for column in columns}
    today = datetime.now().date()

    column_counts = {column['slug']: 0 for column in columns}
    priority_counts = {'Alta': 0, 'Media': 0, 'Baixa': 0}
    overdue_total = 0
    impedido_total = 0
    user_groups = {}

    for card in cards:
        column_counts[card['column_id']] = column_counts.get(card['column_id'], 0) + 1
        priority = card.get('priority') or 'Media'
        priority_counts[priority] = priority_counts.get(priority, 0) + 1
        if card.get('impedido'):
            impedido_total += 1

        card['is_overdue'] = _is_card_overdue(card, today)
        if card['is_overdue']:
            overdue_total += 1
        card['column'] = columns_by_slug.get(card['column_id'])

        uid = card.get('assigned_to')
        group = user_groups.setdefault(uid, {'user': users_by_id.get(uid), 'tasks': []})
        group['tasks'].append(card)

    user_cards = []
    for uid, group in user_groups.items():
        counts = _sprint_task_counts(group['tasks'])
        tasks_sorted = sorted(
            group['tasks'],
            key=lambda card: (card['column_id'] == DONE_COLUMN_SLUG, not card['is_overdue'], card['title'] or '')
        )
        user_cards.append({
            'user': group['user'],
            'user_name': group['user']['name'] if group['user'] else 'Sem responsável',
            'user_color': group['user']['color'] if group['user'] and group['user'].get('color') else '#94a3b8',
            'tasks': tasks_sorted,
            'counts': counts
        })
    user_cards.sort(key=lambda group: (-group['counts']['total'], group['user_name']))

    return {
        'user_cards': user_cards,
        'column_counts': column_counts,
        'priority_counts': priority_counts,
        'overdue_total': overdue_total,
        'impedido_total': impedido_total,
        'counts': _sprint_task_counts(cards)
    }


@kanban_bp.route('/sprints')
@login_required
def sprints_home():
    sprints = get_sprints()
    all_cards = _get_all_cards_including_archived(sprints=sprints)
    cards_by_sprint = {}
    for card in all_cards:
        if card.get('sprint_id'):
            cards_by_sprint.setdefault(card['sprint_id'], []).append(card)

    for sprint in sprints:
        sprint['task_counts'] = _sprint_task_counts(cards_by_sprint.get(sprint['id'], []))

    return render_template(
        'kanban/sprints_home.html',
        sprints=sprints,
        sprint_status_options=get_sprint_status_options(),
        can_manage_sprints=current_user.is_admin
    )


@kanban_bp.route('/sprints/<int:sprint_id>')
@login_required
def sprint_detail(sprint_id):
    sprint = get_sprint_by_id(sprint_id)
    if not sprint:
        return "Sprint not found", 404

    users = get_users()
    columns = get_board_columns()
    all_cards = _get_all_cards_including_archived()
    cards = [card for card in all_cards if card.get('sprint_id') == sprint_id]
    dashboard = _build_sprint_dashboard(cards, users, columns)

    return render_template(
        'kanban/sprint_detail.html',
        sprint=sprint,
        columns=columns,
        **dashboard
    )


@kanban_bp.route('/columns/create', methods=['POST'])
@login_required
def add_column():
    if not current_user.is_admin:
        flash('Apenas administradores podem criar colunas.', 'danger')
        return redirect(url_for('kanban.board'))

    column_name = request.form.get('column_name')
    column_color = request.form.get('column_color')

    try:
        create_column(column_name, column_color)
        flash('Coluna criada com sucesso.', 'success')
    except ValueError as exc:
        flash(str(exc), 'danger')

    return redirect(url_for('kanban.board'))


@kanban_bp.route('/columns/update/<column_id>', methods=['POST'])
@login_required
def edit_column(column_id):
    if not current_user.is_admin:
        flash('Apenas administradores podem editar colunas.', 'danger')
        return redirect(url_for('kanban.board'))

    column_name = request.form.get('column_name')
    column_color = request.form.get('column_color')

    try:
        update_column(column_id, column_name, column_color)
        flash('Coluna atualizada com sucesso.', 'success')
    except ValueError as exc:
        flash(str(exc), 'danger')

    return redirect(url_for('kanban.board'))


@kanban_bp.route('/columns/reorder', methods=['POST'])
@login_required
def reorder_board_columns():
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Apenas administradores podem reordenar colunas.'}), 403

    data = request.json or {}
    column_ids = data.get('column_ids') or []

    try:
        reorder_columns(column_ids)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({'success': True})


@kanban_bp.route('/columns/delete/<column_id>', methods=['POST'])
@login_required
def remove_column(column_id):
    if not current_user.is_admin:
        flash('Apenas administradores podem remover colunas.', 'danger')
        return redirect(url_for('kanban.board'))

    try:
        delete_column(column_id)
        flash('Coluna removida com sucesso. Os cards foram movidos para a primeira coluna disponivel.', 'success')
    except ValueError as exc:
        flash(str(exc), 'danger')

    return redirect(url_for('kanban.board'))


@kanban_bp.route('/users')
@login_required
def users_list():
    return redirect(url_for('users.index'))


@kanban_bp.route('/users/create', methods=['POST'])
@login_required
def add_user():
    return redirect(url_for('users.index'))


@kanban_bp.route('/users/update/<user_id>', methods=['POST'])
@login_required
def edit_user(_user_id):
    return redirect(url_for('users.index'))


@kanban_bp.route('/users/delete/<user_id>', methods=['POST'])
@login_required
def remove_user(_user_id):
    return redirect(url_for('users.index'))


@kanban_bp.route('/card/create', methods=['POST'])
@login_required
def add_card():
    title = request.form.get('title')
    description = request.form.get('description')
    column_id = request.form.get('column_id')
    assigned_to = request.form.get('assigned_to')
    priority = request.form.get('priority', 'Media')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    sprint_id = request.form.get('sprint_id') or None
    impedido = 1 if request.form.get('impedido') else 0
    impedimento = request.form.get('impedimento') or None
    project_task_ref = request.form.get('project_task_ref')  # formato "projId:stepIdx:subIdx"

    if not getattr(current_user, 'is_admin', False):
        assigned_to = str(getattr(current_user, 'id', ''))
        sprint_id = None
        project_task_ref = None

    project_ref = None
    if project_task_ref:
        parts = project_task_ref.split(':')
        if len(parts) == 3:
            if int(parts[2]) != -1:
                flash('Subtarefas nao podem mais ser adicionadas ao Kanban.', 'danger')
                return redirect(url_for('kanban.board'))
            project_ref = {
                'project_id': parts[0],
                'step_index': int(parts[1]),
                'substep_index': int(parts[2])
            }
            # Se importou do projeto e não tem titulo/desc, usa do projeto
            # Mas aqui assumimos que o form envia o titulo/desc ou o backend busca
            # Por simplicidade, o form deve preencher o titulo se selecionar projeto

    if title:
        try:
            create_card(title, description, column_id=column_id, assigned_to=assigned_to, project_ref=project_ref,
                        priority=priority, start_date=start_date, end_date=end_date,
                        impedido=impedido, impedimento=impedimento, sprint_id=sprint_id)
        except ValueError as exc:
            flash(str(exc), 'danger')
        except sqlite3.IntegrityError:
            flash('Nao foi possivel criar a tarefa. Verifique o responsavel e a sprint selecionados.', 'danger')

    return redirect(url_for('kanban.board'))


@kanban_bp.route('/card/<card_id>/subtask/<int:substep_index>/toggle', methods=['POST'])
@login_required
def toggle_card_subtask(card_id, substep_index):
    card = get_card_by_id(card_id)
    if not card:
        return jsonify({'success': False, 'message': 'Card nao encontrado.'}), 404

    project_id = card.get('project_id')
    step_index = card.get('step_index')
    card_substep_index = card.get('substep_index')
    if not project_id or step_index is None or card_substep_index is None or int(card_substep_index) != -1:
        return jsonify({'success': False, 'message': 'Este card nao representa uma tarefa vinculada a projeto.'}), 400

    project = get_project(project_id)
    if not project:
        return jsonify({'success': False, 'message': 'Projeto nao encontrado.'}), 404

    target_substep = None
    for step in project.get('steps', []):
        if int(step.get('ordem', -1)) != int(step_index):
            continue
        for substep in step.get('substeps', []):
            if int(substep.get('ordem', -1)) == int(substep_index):
                target_substep = substep
                break
        if target_substep:
            break

    if not target_substep:
        return jsonify({'success': False, 'message': 'Subtarefa nao encontrada.'}), 404
    if not _can_toggle_project_subtask(target_substep, parent_card=card):
        return jsonify({'success': False, 'message': 'Voce nao tem permissao para alterar esta subtarefa.'}), 403

    payload = request.get_json(silent=True) or {}
    checked_value = payload.get('checked')
    checked = checked_value is True or str(checked_value).lower() in {'1', 'true', 'on', 'yes'}
    target_status = DONE_COLUMN_SLUG if checked else get_default_column_slug()

    updated_project = update_project_substep(project_id, int(step_index), substep_index, target_status, sync_kanban=False)
    if not updated_project:
        return jsonify({'success': False, 'message': 'Nao foi possivel atualizar a subtarefa.'}), 400

    updated_substep = None
    for step in updated_project.get('steps', []):
        if int(step.get('ordem', -1)) != int(step_index):
            continue
        for substep in step.get('substeps', []):
            if int(substep.get('ordem', -1)) == int(substep_index):
                updated_substep = substep
                break
        if updated_substep:
            break

    if not updated_substep:
        return jsonify({'success': False, 'message': 'Subtarefa nao encontrada apos a atualizacao.'}), 404

    return jsonify({
        'success': True,
        'subtask': {
            'substep_index': int(updated_substep.get('ordem', substep_index)),
            'name': updated_substep.get('name'),
            'is_done': updated_substep.get('status') == DONE_COLUMN_SLUG,
            'completed_at': updated_substep.get('completed_at') or '',
        }
    })


@kanban_bp.route('/card/move', methods=['POST'])
def move_card():
    data = request.json
    card_id = data.get('card_id')
    new_column_id = data.get('new_column_id')

    if update_card_position(card_id, new_column_id):
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@kanban_bp.route('/card/reorder', methods=['POST'])
def reorder_cards():
    data = request.json or {}
    card_ids = data.get('card_ids') or []

    try:
        card_ids = [int(card_id) for card_id in card_ids]
    except (TypeError, ValueError):
        return jsonify({'success': False}), 400

    if not card_ids:
        return jsonify({'success': False}), 400

    reorder_column_cards(card_ids)
    return jsonify({'success': True})


@kanban_bp.route('/card/update/<card_id>', methods=['POST'])
@login_required
def edit_card(card_id):
    card = get_card_by_id(card_id)
    if not card:
        flash('Tarefa nao encontrada.', 'danger')
        return redirect(url_for('kanban.board'))

    if not _can_edit_card(card):
        flash('Apenas administradores ou o responsavel pela tarefa podem edita-la.', 'danger')
        return redirect(url_for('kanban.board'))

    title = request.form.get('title')
    description = request.form.get('description')
    assigned_to = request.form.get('assigned_to')
    priority = request.form.get('priority')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    sprint_id = request.form.get('sprint_id') or None
    impedido = 1 if request.form.get('impedido') else 0
    impedimento = request.form.get('impedimento') or None

    if not current_user.is_admin:
        title = card.get('nome_tarefa')
        assigned_to = card.get('responsavel')
        priority = card.get('prioridade')
        start_date = card.get('data_inicio')
        end_date = card.get('data_fim')
        sprint_id = card.get('sprint_id')

    update_card_details(card_id, title, description, assigned_to, priority, start_date, end_date,
                        impedido=impedido, impedimento=impedimento, sprint_id=sprint_id)
    return redirect(url_for('kanban.board'))


_ALLOWED_SPRINT_REDIRECTS = {'kanban.board', 'kanban.sprints_home'}


def _sprint_redirect_target():
    next_endpoint = request.form.get('next')
    if next_endpoint in _ALLOWED_SPRINT_REDIRECTS:
        return redirect(url_for(next_endpoint))
    return redirect(url_for('kanban.board'))


@kanban_bp.route('/sprints/create', methods=['POST'])
@login_required
def add_sprint():
    if not current_user.is_admin:
        flash('Apenas administradores podem cadastrar sprints.', 'danger')
        return _sprint_redirect_target()

    try:
        create_sprint(
            request.form.get('name'),
            description=request.form.get('description'),
            status=request.form.get('status'),
            start_date=request.form.get('start_date'),
            end_date=request.form.get('end_date'),
            created_by_name=current_user.name
        )
        flash('Sprint cadastrada com sucesso.', 'success')
    except ValueError as exc:
        flash(str(exc), 'danger')
    return _sprint_redirect_target()


@kanban_bp.route('/sprints/update/<int:sprint_id>', methods=['POST'])
@login_required
def edit_sprint(sprint_id):
    if not current_user.is_admin:
        flash('Apenas administradores podem editar sprints.', 'danger')
        return redirect(url_for('kanban.board'))

    try:
        update_sprint(
            sprint_id,
            request.form.get('name'),
            description=request.form.get('description'),
            project_id=request.form.get('project_id'),
            status=request.form.get('status'),
            start_date=request.form.get('start_date'),
            end_date=request.form.get('end_date')
        )
        flash('Sprint atualizada com sucesso.', 'success')
    except ValueError as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('kanban.board'))


@kanban_bp.route('/sprints/delete/<int:sprint_id>', methods=['POST'])
@login_required
def remove_sprint(sprint_id):
    if not current_user.is_admin:
        flash('Apenas administradores podem remover sprints.', 'danger')
        return redirect(url_for('kanban.board'))

    try:
        delete_sprint(sprint_id)
        flash('Sprint removida com sucesso.', 'success')
    except ValueError as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('kanban.board'))


@kanban_bp.route('/card/add_note/<card_id>', methods=['POST'])
@login_required
def add_card_note_route(card_id):
    note = request.form.get('note')
    uploaded_files = request.files.getlist('images')
    image_paths = []
    valid_files = [file for file in uploaded_files if file and allowed_file(file.filename)]

    if len(valid_files) > 5:
        return _redirect_back_with_note(card_id, 'images_limit')

    if valid_files:
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        for index, file in enumerate(valid_files):
            filename = secure_filename(file.filename)
            unique_filename = f"{card_id}_{timestamp}_{index}_{filename}"
            image_paths.append(save_upload(
                file,
                object_key=f'kanban_notes/{unique_filename}',
                local_relative_path=f'uploads/kanban_notes/{unique_filename}'
            ))

    result = add_card_note(card_id, note, current_user.name, image_paths=image_paths)
    return _redirect_back_with_note(card_id, result['status'])


@kanban_bp.route('/card/archive/<card_id>')
@login_required
def archive_card_route(card_id):
    if not current_user.is_admin:
        flash('Apenas administradores podem arquivar tarefas.', 'danger')
        return redirect(url_for('kanban.board'))

    archive_card(card_id)
    flash('Tarefa arquivada com sucesso.', 'success')
    return redirect(url_for('kanban.board'))


@kanban_bp.route('/card/unarchive/<card_id>')
@login_required
def unarchive_card_route(card_id):
    if not current_user.is_admin:
        flash('Apenas administradores podem restaurar tarefas.', 'danger')
        return redirect(url_for('kanban.archived_board'))

    unarchive_card(card_id)
    flash('Tarefa restaurada com sucesso.', 'success')
    return redirect(url_for('kanban.archived_board'))


@kanban_bp.route('/card/delete/<card_id>')
@login_required
def remove_card(card_id):
    if not current_user.is_admin:
        flash('Apenas administradores podem excluir tarefas.', 'danger')
        return redirect(url_for('kanban.board'))

    delete_card(card_id)
    flash('Tarefa excluída com sucesso.', 'success')

    if request.args.get('redirect_archived'):
        return redirect(url_for('kanban.archived_board'))

    return redirect(url_for('kanban.board'))
