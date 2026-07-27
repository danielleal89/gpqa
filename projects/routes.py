from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
import json
from werkzeug.utils import secure_filename
from . import projects_bp
try:
    from ..storage import save_upload, delete_file
except (ImportError, ValueError):
    from storage import save_upload, delete_file  # type: ignore
from .models import (
    load_projects, create_project, get_project, update_project_step,
    update_project_status, update_project_substep, update_substep_details,
    add_project_substep, add_project_step, update_project_step_name,
    update_project_substep_name, update_project_name, update_project_step_order,
    delete_project_step,
    get_status_columns, get_project_status_options,
    create_project_step_kanban_card, create_project_substep_kanban_card,
    add_step_kanban_note, add_substep_kanban_note, add_project_documentation, delete_project_documentation
)
try:
    from ..kanban.models import get_board_data, get_sprints
except (ImportError, ValueError):
    from kanban.models import get_board_data, get_sprints  # type: ignore

# Configuração para uploads
UPLOAD_FOLDER = 'static/uploads/projects'
PROJECT_DOCUMENTATION_FOLDER = 'static/uploads/project_documentations'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_DOCUMENTATION_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'doc', 'docx'}
ALLOWED_DOCUMENTATION_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_documentation_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_DOCUMENTATION_EXTENSIONS


def allowed_documentation_image(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_DOCUMENTATION_IMAGE_EXTENSIONS


def _is_admin_user():
    return bool(getattr(current_user, 'is_authenticated', False) and getattr(current_user, 'is_admin', False))


def _is_async_request():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _can_add_substep_note(project, step_index, substep_index):
    if _is_admin_user():
        return True
    if not getattr(current_user, 'is_authenticated', False):
        return False

    try:
        substep = project['steps'][step_index]['substeps'][substep_index]
    except (IndexError, KeyError, TypeError):
        return False

    kanban_card = substep.get('kanban_card') or {}
    return str(kanban_card.get('assigned_to_id') or '') == str(getattr(current_user, 'id', ''))


def _can_add_step_note(project, step_index):
    if _is_admin_user():
        return True
    if not getattr(current_user, 'is_authenticated', False):
        return False

    try:
        step = project['steps'][step_index]
    except (IndexError, KeyError, TypeError):
        return False

    kanban_card = step.get('kanban_card') or {}
    return str(kanban_card.get('assigned_to_id') or '') == str(getattr(current_user, 'id', ''))


def _build_project_sprint_overview(project_id):
    sprints = get_sprints()
    board_data = get_board_data(sprints=sprints)
    sprint_lookup = {int(sprint['id']): sprint for sprint in sprints}
    project_id_str = str(project_id)

    sprint_entries = {}
    sprint_order = {}
    for index, sprint in enumerate(sprints):
        sprint_order[int(sprint['id'])] = index
        if str(sprint.get('project_id') or '') == project_id_str:
            sprint_entries[int(sprint['id'])] = {
                **dict(sprint),
                'total_cards': 0,
                'done_cards': 0,
                'task_progress_percent': 0
            }

    for card in board_data.get('cards', []):
        project_ref = card.get('project_ref') or {}
        if str(project_ref.get('project_id') or '') != project_id_str:
            continue

        sprint_id = card.get('sprint_id')
        if not sprint_id or sprint_id not in sprint_lookup:
            continue

        if sprint_id not in sprint_entries:
            sprint_entries[sprint_id] = {
                **dict(sprint_lookup[sprint_id]),
                'total_cards': 0,
                'done_cards': 0,
                'task_progress_percent': 0
            }

        entry = sprint_entries[sprint_id]
        is_done = card.get('column_id') == 'done'
        entry['total_cards'] += 1
        if is_done:
            entry['done_cards'] += 1

    sprint_list = list(sprint_entries.values())
    for sprint in sprint_list:
        total_cards = sprint['total_cards']
        sprint['task_progress_percent'] = round((sprint['done_cards'] / total_cards) * 100) if total_cards else 0

    sprint_list.sort(
        key=lambda sprint: (
            sprint_order.get(int(sprint['id']), 9999),
            sprint.get('start_date') or '9999-12-31',
            -int(sprint['id'])
        )
    )
    return sprint_list


def _parse_project_date(value):
    clean_value = (value or '').strip()
    if not clean_value:
        return None

    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y', '%d/%m/%Y %H:%M'):
        try:
            return datetime.strptime(clean_value, fmt)
        except ValueError:
            continue
    return None


def _build_project_dashboard(project, project_sprints):
    steps = project.get('steps', [])
    substeps = [substep for step in steps for substep in step.get('substeps', [])]
    all_items = steps + substeps
    today = datetime.now().date()
    status_columns = get_status_columns()

    task_total = len(steps)
    task_done = sum(1 for step in steps if step.get('status') == 'done')
    subtask_total = len(substeps)
    subtask_done = sum(1 for substep in substeps if substep.get('status') == 'done')
    items_total = len(all_items)
    items_done = sum(1 for item in all_items if item.get('status') == 'done')
    linked_cards = [
        item.get('kanban_card')
        for item in all_items
        if item.get('kanban_card')
    ]
    linked_cards = [card for card in linked_cards if card]
    linked_cards_total = len(linked_cards)
    linked_cards_done = sum(1 for card in linked_cards if card.get('coluna') == 'done')
    linked_cards_impeded = sum(1 for card in linked_cards if card.get('impedido'))
    linked_cards_without_owner = sum(1 for card in linked_cards if not card.get('assigned_to_id'))

    overdue_cards = 0
    next_due_card = None
    next_due_date = None
    for card in linked_cards:
        end_date = _parse_project_date(card.get('data_fim'))
        is_done = card.get('coluna') == 'done'
        if end_date and not is_done:
            if end_date.date() < today:
                overdue_cards += 1
            if next_due_date is None or end_date.date() < next_due_date:
                next_due_date = end_date.date()
                next_due_card = card

    status_task_counts = {column['name']: 0 for column in status_columns}
    status_subtask_counts = {column['name']: 0 for column in status_columns}
    for step in steps:
        status_task_counts[step.get('status_name') or 'Sem status'] = status_task_counts.get(step.get('status_name') or 'Sem status', 0) + 1
    for substep in substeps:
        status_subtask_counts[substep.get('status_name') or 'Sem status'] = status_subtask_counts.get(substep.get('status_name') or 'Sem status', 0) + 1

    sprint_status_counts = {
        'Planejada': sum(1 for sprint in project_sprints if sprint.get('status') == 'planned'),
        'Ativa': sum(1 for sprint in project_sprints if sprint.get('status') == 'active'),
        'Concluida': sum(1 for sprint in project_sprints if sprint.get('status') == 'completed'),
    }

    attention_items = []
    if overdue_cards:
        attention_items.append(f'{overdue_cards} card(s) do projeto estao atrasados no Kanban.')
    if linked_cards_impeded:
        attention_items.append(f'{linked_cards_impeded} card(s) estao marcados com impedimento.')
    missing_kanban_cards = items_total - linked_cards_total
    if missing_kanban_cards:
        attention_items.append(f'{missing_kanban_cards} item(ns) do projeto ainda nao foram vinculados ao Kanban.')
    if not attention_items:
        attention_items.append('Nenhum alerta principal no momento.')

    task_status_breakdown = [
        {
            'label': column['name'],
            'count': status_task_counts.get(column['name'], 0),
            'percent': round((status_task_counts.get(column['name'], 0) / task_total) * 100) if task_total else 0,
        }
        for column in status_columns
    ]
    subtask_status_breakdown = [
        {
            'label': column['name'],
            'count': status_subtask_counts.get(column['name'], 0),
            'percent': round((status_subtask_counts.get(column['name'], 0) / subtask_total) * 100) if subtask_total else 0,
        }
        for column in status_columns
    ]
    sprint_status_breakdown = [
        {
            'label': label,
            'count': count,
            'percent': round((count / len(project_sprints)) * 100) if project_sprints else 0,
        }
        for label, count in sprint_status_counts.items()
    ]

    return {
        'task_total': task_total,
        'task_done': task_done,
        'task_progress_percent': round((task_done / task_total) * 100) if task_total else 0,
        'subtask_total': subtask_total,
        'subtask_done': subtask_done,
        'subtask_progress_percent': round((subtask_done / subtask_total) * 100) if subtask_total else 0,
        'items_total': items_total,
        'items_done': items_done,
        'overall_progress_percent': round((items_done / items_total) * 100) if items_total else 0,
        'documentation_total': len(project.get('documentation_items', [])),
        'linked_cards_total': linked_cards_total,
        'linked_cards_done': linked_cards_done,
        'linked_cards_progress_percent': round((linked_cards_done / linked_cards_total) * 100) if linked_cards_total else 0,
        'linked_cards_impeded': linked_cards_impeded,
        'linked_cards_without_owner': linked_cards_without_owner,
        'overdue_cards': overdue_cards,
        'sprints_total': len(project_sprints),
        'sprint_status_counts': sprint_status_counts,
        'active_sprints': [sprint for sprint in project_sprints if sprint.get('status') == 'active'],
        'task_status_counts': status_task_counts,
        'subtask_status_counts': status_subtask_counts,
        'task_status_breakdown': task_status_breakdown,
        'subtask_status_breakdown': subtask_status_breakdown,
        'sprint_status_breakdown': sprint_status_breakdown,
        'attention_items': attention_items,
        'next_due_card': next_due_card,
        'next_due_date_display': next_due_date.strftime('%d/%m/%Y') if next_due_date else '',
    }


@projects_bp.route('/')
def index():
    projects = load_projects()
    return render_template(
        'projects/index.html',
        projects=projects,
        can_create_project=_is_admin_user()
    )


@projects_bp.route('/create', methods=['GET', 'POST'])
def create():
    if not _is_admin_user():
        flash('Apenas administradores podem criar novos projetos.', 'danger')
        return redirect(url_for('projects.index'))

    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')

        # Recupera os passos enviados como JSON string hidden input ou processa os campos dinâmicos
        # Como o form vai ser dinâmico, vamos tentar pegar o JSON
        steps_json = request.form.get('steps_data')

        steps_data = []
        if steps_json:
            try:
                steps_data = json.loads(steps_json)
            except json.JSONDecodeError:
                print("Erro ao decodificar JSON de passos")

        creator_name = current_user.name if getattr(current_user, 'is_authenticated', False) else 'Não informado'
        create_project(name, description, steps_data, created_by_name=creator_name)
        return redirect(url_for('projects.index'))

    preview_creator_name = current_user.name if getattr(current_user, 'is_authenticated', False) else 'Não informado'
    preview_created_at = datetime.now().strftime('%d/%m/%Y %H:%M')
    return render_template(
        'projects/create.html',
        preview_creator_name=preview_creator_name,
        preview_created_at=preview_created_at
    )


@projects_bp.route('/<project_id>')
def detail(project_id):
    project = get_project(project_id)
    if not project:
        return "Project not found", 404

    project_sprints = _build_project_sprint_overview(project_id)
    can_manage_project = _is_admin_user()
    project['can_manage_project'] = can_manage_project
    for step in project.get('steps', []):
        step['can_edit_name'] = can_manage_project
        step['can_add_substep'] = can_manage_project
        step['can_add_to_kanban'] = can_manage_project and not step.get('has_kanban_card')
        can_add_step_note = bool(
            step.get('has_kanban_card')
            and step.get('kanban_notes_count', 0) < 10
            and _can_add_step_note(project, step.get('ordem'))
        )
        step['can_add_kanban_note'] = can_add_step_note
        step['kanban_note_limit_reached'] = bool(
            step.get('has_kanban_card') and step.get('kanban_notes_count', 0) >= 10
        )
        step['kanban_note_permission_denied'] = bool(
            step.get('has_kanban_card')
            and not can_add_step_note
            and not step['kanban_note_limit_reached']
        )

        for substep in step.get('substeps', []):
            substep['can_edit_name'] = can_manage_project
            can_add_note = bool(
                substep.get('has_kanban_card')
                and substep.get('kanban_notes_count', 0) < 10
                and _can_add_substep_note(project, step.get('ordem'), substep.get('ordem'))
            )
            substep['can_add_to_kanban'] = can_manage_project and not substep.get('has_kanban_card')
            substep['can_add_kanban_note'] = can_add_note
            substep['kanban_note_limit_reached'] = bool(
                substep.get('has_kanban_card') and substep.get('kanban_notes_count', 0) >= 10
            )
            substep['kanban_note_permission_denied'] = bool(
                substep.get('has_kanban_card')
                and not can_add_note
                and not substep['kanban_note_limit_reached']
            )

    project_dashboard = _build_project_dashboard(project, project_sprints)

    return render_template(
        'projects/detail.html',
        project=project,
        project_sprints=project_sprints,
        project_dashboard=project_dashboard,
        status_columns=get_status_columns(),
        project_status_options=get_project_status_options(),
        open_description=request.args.get('open_description') == '1'
    )


@projects_bp.route('/<project_id>/add_documentation', methods=['POST'])
def add_documentation(project_id):
    document_title = (request.form.get('document_title') or '').strip()
    documentation_type = (request.form.get('documentation_type') or 'image').strip().lower()
    uploaded_image = request.files.get('document_image')
    uploaded_file = request.files.get('document_file')
    link_url = (request.form.get('link_url') or '').strip()

    if not document_title:
        flash('Informe um nome para a documentação.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

    if documentation_type not in {'image', 'file', 'link'}:
        flash('Selecione um tipo válido para a documentação.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

    file_entries = []
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    if documentation_type == 'image':
        has_image = bool(uploaded_image and uploaded_image.filename)
        if not has_image:
            flash('Selecione uma imagem para a documentação.', 'danger')
            return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

        if not allowed_documentation_image(uploaded_image.filename):
            flash('Tipo de imagem não permitido para a documentação.', 'danger')
            return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

        original_name = secure_filename(uploaded_image.filename)
        unique_filename = f'{timestamp}_{original_name}'
        stored_path = save_upload(
            uploaded_image,
            object_key=f'project_documentations/{project_id}/{unique_filename}',
            local_relative_path=f'uploads/project_documentations/{project_id}/{unique_filename}'
        )
        file_entries.append({
            'title': document_title,
            'file_name': original_name,
            'file_path': stored_path,
            'mime_type': uploaded_image.mimetype or ''
        })
    elif documentation_type == 'file':
        has_file = bool(uploaded_file and uploaded_file.filename)
        if not has_file:
            flash('Selecione um arquivo para a documentação.', 'danger')
            return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

        if not allowed_documentation_file(uploaded_file.filename):
            flash('Tipo de arquivo não permitido para a documentação.', 'danger')
            return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

        original_name = secure_filename(uploaded_file.filename)
        unique_filename = f'{timestamp}_{original_name}'
        stored_path = save_upload(
            uploaded_file,
            object_key=f'project_documentations/{project_id}/{unique_filename}',
            local_relative_path=f'uploads/project_documentations/{project_id}/{unique_filename}'
        )
        file_entries.append({
            'title': document_title,
            'file_name': original_name,
            'file_path': stored_path,
            'mime_type': uploaded_file.mimetype or ''
        })
    else:
        if not link_url:
            flash('Informe um link para a documentação.', 'danger')
            return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

    result = add_project_documentation(
        project_id,
        file_entries=file_entries,
        link_url=link_url,
        link_title=document_title,
        created_by_name=current_user.name if getattr(current_user, 'is_authenticated', False) else ''
    )
    if not result.get('success'):
        flash('Não foi possível salvar a documentação informada.', 'danger')
    return redirect(url_for('projects.detail', project_id=project_id, open_description=1))


@projects_bp.route('/<project_id>/delete_documentation/<int:documentation_id>', methods=['POST'])
def remove_documentation(project_id, documentation_id):
    if not _is_admin_user():
        flash('Apenas administradores podem excluir documentações.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

    result = delete_project_documentation(project_id, documentation_id)
    if not result.get('success'):
        flash('Documentação não encontrada.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id, open_description=1))

    file_path = (result.get('file_path') or '').strip()
    if file_path:
        delete_file(file_path)

    flash('Documentação excluída com sucesso.', 'success')
    return redirect(url_for('projects.detail', project_id=project_id, open_description=1))


@projects_bp.route('/<project_id>/update_step/<int:step_index>', methods=['POST'])
def update_step(project_id, step_index):
    status = request.form.get('status')
    update_project_step(project_id, step_index, status)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_name', methods=['POST'])
def update_name(project_id):
    if not _is_admin_user():
        flash('Apenas administradores podem alterar o nome do projeto.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    project_name = request.form.get('project_name')
    if project_name:
        update_project_name(project_id, project_name)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_step_name/<int:step_index>', methods=['POST'])
def update_step_name(project_id, step_index):
    if not _is_admin_user():
        flash('Apenas administradores podem alterar o nome das tarefas.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    step_name = request.form.get('step_name')
    if step_name:
        update_project_step_name(project_id, step_index, step_name)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_step_order/<int:step_index>', methods=['POST'])
def update_step_order(project_id, step_index):
    if not _is_admin_user():
        flash('Apenas administradores podem alterar a ordem das tarefas.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    target_order_input = request.form.get('target_order')
    reorder_mode = request.form.get('reorder_mode') or 'insert'
    redirect_kwargs = {'project_id': project_id}
    try:
        target_order = max(int(target_order_input or ''), 1) - 1
    except ValueError:
        flash('Informe um numero de ordem valido para a tarefa.', 'danger')
        return redirect(url_for('projects.detail', **redirect_kwargs))

    result = update_project_step_order(project_id, step_index, target_order, reorder_mode)
    if not result.get('success'):
        flash('Nao foi possivel alterar a ordem da tarefa.', 'danger')
        redirect_kwargs['open_task'] = step_index
    elif result.get('status') == 'swap':
        flash('A ordem da tarefa foi trocada com sucesso.', 'success')
        redirect_kwargs['open_task'] = target_order
    elif result.get('status') == 'insert':
        flash('A tarefa foi reposicionada com sucesso.', 'success')
        redirect_kwargs['open_task'] = target_order
    return redirect(url_for('projects.detail', **redirect_kwargs))


@projects_bp.route('/<project_id>/delete_step/<int:step_index>', methods=['POST'])
def delete_step(project_id, step_index):
    if not _is_admin_user():
        if _is_async_request():
            return jsonify({'success': False, 'message': 'Apenas administradores podem excluir tarefas.'}), 403
        flash('Apenas administradores podem excluir tarefas.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    result = delete_project_step(project_id, step_index)
    if not result.get('success'):
        if _is_async_request():
            return jsonify({'success': False, 'message': 'Nao foi possivel excluir a tarefa.'}), 400
        flash('Nao foi possivel excluir a tarefa.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id, open_task=step_index))

    for file_path in result.get('deleted_file_paths', []):
        delete_file(file_path)

    deleted_substeps = result.get('deleted_substep_names', [])
    if deleted_substeps:
        success_message = f"Tarefa excluida com sucesso. {len(deleted_substeps)} subtarefa(s) vinculada(s) tambem foram removidas."
        if _is_async_request():
            return jsonify({
                'success': True,
                'message': success_message,
                'deleted_substeps_count': len(deleted_substeps),
            })
        flash(
            success_message,
            'success'
        )
    else:
        if _is_async_request():
            return jsonify({'success': True, 'message': 'Tarefa excluida com sucesso.', 'deleted_substeps_count': 0})
        flash('Tarefa excluida com sucesso.', 'success')
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_substep/<int:step_index>/<int:substep_index>', methods=['POST'])
def update_substep(project_id, step_index, substep_index):
    status = request.form.get('status')
    update_project_substep(project_id, step_index, substep_index, status)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_substep_name/<int:step_index>/<int:substep_index>', methods=['POST'])
def update_substep_name(project_id, step_index, substep_index):
    if not _is_admin_user():
        flash('Apenas administradores podem alterar o nome das subtarefas.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id, open_detail=f'{step_index}-{substep_index}'))

    substep_name = request.form.get('substep_name')
    if substep_name:
        update_project_substep_name(project_id, step_index, substep_index, substep_name)
    return redirect(url_for('projects.detail', project_id=project_id, open_detail=f'{step_index}-{substep_index}'))


@projects_bp.route('/<project_id>/update_status', methods=['POST'])
def update_status(project_id):
    if not getattr(current_user, 'is_admin', False):
        flash('Apenas administradores podem alterar o status do projeto.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    status = request.form.get('status')
    update_project_status(project_id, status)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_substep_details/<int:step_index>/<int:substep_index>', methods=['POST'])
def update_substep_details_route(project_id, step_index, substep_index):
    notes = request.form.get('notes')
    links_text = request.form.get('links')

    links = []
    if links_text:
        # Separa por nova linha e remove espaços vazios
        links = [link.strip() for link in links_text.split('\n') if link.strip()]

    uploaded_files = request.files.getlist('images')
    image_paths = []

    if uploaded_files:
        for file in uploaded_files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{project_id}_{step_index}_{substep_index}_{filename}"
                image_paths.append(save_upload(
                    file,
                    object_key=f'projects/{unique_filename}',
                    local_relative_path=f'uploads/projects/{unique_filename}'
                ))

    # Se não houver upload, passa None para não alterar (ou lista vazia se quisesse limpar, mas o model faz extend)
    # O model faz extend para imagens. Então passamos apenas as novas.
    images_to_pass = image_paths if image_paths else []

    update_substep_details(project_id, step_index, substep_index, notes=notes, links=links, new_images=images_to_pass)

    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/add_substep/<int:step_index>', methods=['POST'])
def add_substep(project_id, step_index):
    if not _is_admin_user():
        flash('Apenas administradores podem criar novas subtarefas.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    substep_name = request.form.get('substep_name')
    if substep_name:
        add_project_substep(project_id, step_index, substep_name)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/add_step', methods=['POST'])
def add_step(project_id):
    if not _is_admin_user():
        flash('Apenas administradores podem criar novas tarefas.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    step_name = request.form.get('step_name')
    if step_name:
        add_project_step(project_id, step_name)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/add_step_to_kanban/<int:step_index>', methods=['POST'])
def add_step_to_kanban(project_id, step_index):
    if not _is_admin_user():
        flash('Apenas administradores podem adicionar tarefas ao Kanban.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id))

    create_project_step_kanban_card(project_id, step_index)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/add_substep_to_kanban/<int:step_index>/<int:substep_index>', methods=['POST'])
def add_substep_to_kanban(project_id, step_index, substep_index):
    if not _is_admin_user():
        flash('Apenas administradores podem adicionar subtarefas ao Kanban.', 'danger')
        return redirect(url_for('projects.detail', project_id=project_id, open_detail=f'{step_index}-{substep_index}'))

    create_project_substep_kanban_card(project_id, step_index, substep_index)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/add_substep_kanban_note/<int:step_index>/<int:substep_index>', methods=['POST'])
def add_substep_kanban_note_route(project_id, step_index, substep_index):
    project = get_project(project_id)
    if not project:
        return redirect(url_for('projects.detail', project_id=project_id))

    if not _can_add_substep_note(project, step_index, substep_index):
        flash('Apenas administradores ou o responsável pela subtarefa podem adicionar notas.', 'danger')
        return redirect(url_for(
            'projects.detail',
            project_id=project_id,
            open_detail=f'{step_index}-{substep_index}'
        ))

    note = request.form.get('note')
    uploaded_files = request.files.getlist('images')
    image_paths = []
    valid_files = [file for file in uploaded_files if file and allowed_file(file.filename)]

    if len(valid_files) > 5:
        return redirect(url_for(
            'projects.detail',
            project_id=project_id,
            open_detail=f'{step_index}-{substep_index}',
            kanban_note_status='images_limit'
        ))

    if valid_files:
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        for index, file in enumerate(valid_files):
            filename = secure_filename(file.filename)
            unique_filename = f"{project_id}_{step_index}_{substep_index}_{timestamp}_{index}_{filename}"
            image_paths.append(save_upload(
                file,
                object_key=f'kanban_notes/{unique_filename}',
                local_relative_path=f'uploads/kanban_notes/{unique_filename}'
            ))

    result = add_substep_kanban_note(
        project_id,
        step_index,
        substep_index,
        note,
        current_user.name if getattr(current_user, 'is_authenticated', False) else '',
        image_paths=image_paths
    )
    return redirect(url_for(
        'projects.detail',
        project_id=project_id,
        open_detail=f'{step_index}-{substep_index}',
        kanban_note_status=result['status']
    ))


@projects_bp.route('/<project_id>/add_step_kanban_note/<int:step_index>', methods=['POST'])
def add_step_kanban_note_route(project_id, step_index):
    project = get_project(project_id)
    if not project:
        return redirect(url_for('projects.detail', project_id=project_id))

    if not _can_add_step_note(project, step_index):
        flash('Apenas administradores ou o responsável pela tarefa podem adicionar notas.', 'danger')
        return redirect(url_for(
            'projects.detail',
            project_id=project_id,
            open_task=step_index
        ))

    note = request.form.get('note')
    uploaded_files = request.files.getlist('images')
    image_paths = []
    valid_files = [file for file in uploaded_files if file and allowed_file(file.filename)]

    if len(valid_files) > 5:
        return redirect(url_for(
            'projects.detail',
            project_id=project_id,
            open_task=step_index,
            kanban_task_note_status='images_limit'
        ))

    if valid_files:
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        for index, file in enumerate(valid_files):
            filename = secure_filename(file.filename)
            unique_filename = f"{project_id}_{step_index}_task_{timestamp}_{index}_{filename}"
            image_paths.append(save_upload(
                file,
                object_key=f'kanban_notes/{unique_filename}',
                local_relative_path=f'uploads/kanban_notes/{unique_filename}'
            ))

    result = add_step_kanban_note(
        project_id,
        step_index,
        note,
        current_user.name if getattr(current_user, 'is_authenticated', False) else '',
        image_paths=image_paths
    )
    return redirect(url_for(
        'projects.detail',
        project_id=project_id,
        open_task=step_index,
        kanban_task_note_status=result['status']
    ))
