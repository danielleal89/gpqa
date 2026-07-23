from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
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
    update_project_substep_name,
    get_status_columns, get_project_status_options,
    create_project_step_kanban_card, create_project_substep_kanban_card,
    add_step_kanban_note, add_substep_kanban_note, add_project_documentation, delete_project_documentation
)

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


@projects_bp.route('/')
def index():
    projects = load_projects()
    return render_template('projects/index.html', projects=projects)


@projects_bp.route('/create', methods=['GET', 'POST'])
def create():
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

    can_manage_project = _is_admin_user()
    project['can_manage_project'] = can_manage_project
    for step in project.get('steps', []):
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

    return render_template(
        'projects/detail.html',
        project=project,
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


@projects_bp.route('/<project_id>/update_step_name/<int:step_index>', methods=['POST'])
def update_step_name(project_id, step_index):
    step_name = request.form.get('step_name')
    if step_name:
        update_project_step_name(project_id, step_index, step_name)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_substep/<int:step_index>/<int:substep_index>', methods=['POST'])
def update_substep(project_id, step_index, substep_index):
    status = request.form.get('status')
    update_project_substep(project_id, step_index, substep_index, status)
    return redirect(url_for('projects.detail', project_id=project_id))


@projects_bp.route('/<project_id>/update_substep_name/<int:step_index>/<int:substep_index>', methods=['POST'])
def update_substep_name(project_id, step_index, substep_index):
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
