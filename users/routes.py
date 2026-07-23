from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
import base64

try:
    from ..kanban.models import get_users, get_user_by_id, create_user, update_user, delete_user
except (ImportError, ValueError):
    from kanban.models import get_users, get_user_by_id, create_user, update_user, delete_user


users_bp = Blueprint('users', __name__, template_folder='../pages/kanban')


@users_bp.route('/')
@users_bp.route('')
@login_required
def index():
    users = get_users()
    return render_template('users.html', users=users)


@users_bp.route('/create', methods=['POST'])
@login_required
def create():
    name = request.form.get('name')
    key = request.form.get('key')
    password = request.form.get('password')
    color = request.form.get('color', '#3b82f6')
    is_admin = 1 if request.form.get('is_admin') else 0

    photo_file = request.files.get('photo')
    photo_base64 = None
    if photo_file and photo_file.filename:
        try:
            file_content = photo_file.read()
            photo_base64 = base64.b64encode(file_content).decode('utf-8')
        except Exception as e:
            flash(f'Erro ao processar foto: {str(e)}', 'warning')

    if not name or not password:
        flash('Nome e senha sao obrigatorios.', 'danger')
        return redirect(url_for('users.index'))

    try:
        create_user(name, password, key=key, color=color, photo=photo_base64, is_admin=is_admin)
        flash('Usuario criado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao criar usuario: {str(e)}', 'danger')

    return redirect(url_for('users.index'))


@users_bp.route('/update/<user_id>', methods=['POST'])
@login_required
def update(user_id):
    name = request.form.get('name')
    key = request.form.get('key')
    password = request.form.get('password')
    color = request.form.get('color', '#3b82f6')
    is_admin = 1 if request.form.get('is_admin') else 0

    photo_file = request.files.get('photo')
    photo_base64 = None
    if photo_file and photo_file.filename:
        try:
            file_content = photo_file.read()
            photo_base64 = base64.b64encode(file_content).decode('utf-8')
        except Exception as e:
            flash(f'Erro ao processar foto: {str(e)}', 'warning')

    if not name:
        flash('Nome e obrigatorio.', 'danger')
        return redirect(url_for('users.index'))

    try:
        if update_user(user_id, name, password=password, key=key, color=color, photo=photo_base64, is_admin=is_admin):
            flash('Usuario atualizado com sucesso!', 'success')
        else:
            flash('Erro ao atualizar usuario.', 'danger')
    except Exception as e:
        flash(f'Erro ao atualizar usuario: {str(e)}', 'danger')

    return redirect(url_for('users.index'))


@users_bp.route('/delete/<user_id>', methods=['POST'])
@login_required
def delete(user_id):
    password = request.form.get('password')
    user = get_user_by_id(user_id)

    if not user:
        flash('Usuario nao encontrado.', 'danger')
        return redirect(url_for('users.index'))

    if not password:
        flash('Senha e obrigatoria para exclusao.', 'danger')
        return redirect(url_for('users.index'))

    if current_user.verify_password(password):
        delete_user(user_id)
        flash('Usuario excluido com sucesso.', 'success')
    else:
        flash('Senha incorreta (Admin). Nao foi possivel excluir o usuario.', 'danger')

    return redirect(url_for('users.index'))
