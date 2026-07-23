from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
try:
    from ..db import get_db
except (ImportError, ValueError):
    try:
        from plm_qa_dashboard.db import get_db
    except ImportError:
        from db import get_db


class User(UserMixin):
    def __init__(self, id, name, password, is_admin, color, photo, birthday, key=None):
        self.id = str(id)
        self.name = name
        self.password = password
        self.is_admin = is_admin
        self.color = color
        self.photo = photo
        self.birthday = birthday
        self.key = key

    @property
    def avatar_text(self):
        if self.name:
            return self.name[:2].upper()
        return "??"

    @staticmethod
    def get(user_id):
        try:
            db = get_db()
            user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            if not user:
                return None
            return User(
                id=user['id'],
                name=user['name'],
                password=user['password'],
                is_admin=user['is_admin'],
                color=user['color'],
                photo=user['photo'],
                birthday=user['birthday'],
                key=user['key'] if 'key' in user.keys() else None
            )
        except Exception:
            return None

    @staticmethod
    def get_by_name(name):
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE name = ?', (name,)).fetchone()
        if not user:
            return None
        return User(
            id=user['id'],
            name=user['name'],
            password=user['password'],
            is_admin=user['is_admin'],
            color=user['color'],
            photo=user['photo'],
            birthday=user['birthday'],
            key=user['key'] if 'key' in user.keys() else None
        )

    @staticmethod
    def get_by_key(key):
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE key = ?', (key,)).fetchone()
        if not user:
            return None
        return User(
            id=user['id'],
            name=user['name'],
            password=user['password'],
            is_admin=user['is_admin'],
            color=user['color'],
            photo=user['photo'],
            birthday=user['birthday'],
            key=user['key'] if 'key' in user.keys() else None
        )

    def verify_password(self, password):
        if not self.password:
            return False
        return check_password_hash(self.password, password)

    def update_password(self, new_password):
        hashed_password = generate_password_hash(new_password)
        db = get_db()
        db.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, self.id))
        db.commit()
        self.password = hashed_password
