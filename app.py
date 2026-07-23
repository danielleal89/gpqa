import os
import time
from flask import Flask, redirect, url_for, request
from flask_login import LoginManager, current_user
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_FOLDER = os.path.join(BASE_DIR, 'pages')
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=PAGES_FOLDER, static_folder=STATIC_FOLDER)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-123')

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.init_app(app)

# Import models for user loader
try:
    from .auth.models import User
except ImportError:
    from auth.models import User


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


# Database teardown
try:
    from .db import close_connection, init_db
except ImportError:
    from db import close_connection, init_db

try:
    from .storage import build_file_url, serve_file
except ImportError:
    from storage import build_file_url, serve_file
app.teardown_appcontext(close_connection)

# Register Blueprints
try:
    from .auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')
except ImportError:
    from auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

try:
    from .projects import projects_bp
    # projects_bp has url_prefix='/projects' in its __init__.py
    app.register_blueprint(projects_bp)
except ImportError:
    from projects import projects_bp
    app.register_blueprint(projects_bp)

try:
    from .kanban import kanban_bp
    app.register_blueprint(kanban_bp, url_prefix='/kanban')
except ImportError:
    from kanban import kanban_bp
    app.register_blueprint(kanban_bp, url_prefix='/kanban')

try:
    from .users import users_bp
    app.register_blueprint(users_bp, url_prefix='/usuarios')
except ImportError:
    from users import users_bp
    app.register_blueprint(users_bp, url_prefix='/usuarios')

# Root route


@app.before_request
def require_login():
    if not request.endpoint:
        return
    public_endpoints = ['auth.login', 'static']
    if request.endpoint not in public_endpoints and not current_user.is_authenticated:
        return redirect(url_for('auth.login'))


@app.before_request
def start_request_timer():
    request._route_started_at = time.perf_counter()


@app.after_request
def log_route_timing(response):
    enabled = (os.environ.get('LOG_ROUTE_TIMINGS') or '0').strip().lower() in {'1', 'true', 'yes'}
    started_at = getattr(request, '_route_started_at', None)
    if enabled and started_at is not None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        print(f'[route-timing] {request.method} {request.path} -> {response.status_code} in {elapsed_ms:.1f}ms')
    return response


@app.route('/')
def index():
    return redirect(url_for('projects.index'))


@app.route('/storage/file')
def storage_file():
    return serve_file(request.args.get('path'))


app.add_template_global(build_file_url, name='storage_url')


with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
