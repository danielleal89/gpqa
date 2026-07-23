from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from . import auth_bp
from .models import User


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('projects.index'))

    if request.method == 'POST':
        key = request.form.get('key')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False

        user = User.get_by_key(key)

        if not user:
            flash('Chave inválida.', 'danger')
            return redirect(url_for('auth.login'))

        if not user.verify_password(password):
            flash('Senha inválida.', 'danger')
            return redirect(url_for('auth.login'))

        login_user(user, remember=remember)

        response = redirect(url_for('projects.index'))

        if remember:
            # Salva a chave e senha em cookies por 30 dias
            # ATENÇÃO: Salvar senha em cookie não é seguro para produção.
            response.set_cookie('remember_key', key, max_age=30*24*60*60)
            response.set_cookie('remember_password', password, max_age=30*24*60*60)
        else:
            # Se não marcou, remove os cookies se existirem
            response.set_cookie('remember_key', '', expires=0)
            response.set_cookie('remember_password', '', expires=0)

        return response

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not new_password or not confirm_password:
        flash('Preencha todos os campos.', 'danger')
        return redirect(request.referrer or url_for('projects.index'))

    if new_password != confirm_password:
        flash('As senhas não conferem.', 'danger')
        return redirect(request.referrer or url_for('projects.index'))

    try:
        current_user.update_password(new_password)
        flash('Senha alterada com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao alterar senha: {str(e)}', 'danger')

    return redirect(request.referrer or url_for('projects.index'))
