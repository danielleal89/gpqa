import os
import sqlite3
import requests


class D1Error(Exception):
    pass


class D1Cursor:
    def __init__(self, connection):
        self._connection = connection
        self._rows = []
        self._index = 0
        self.lastrowid = None
        self.rowcount = -1
        self.meta = {}

    def execute(self, sql, params=None):
        result = self._connection._query(sql, params=params)
        self.meta = result.get('meta') or {}
        self.lastrowid = self.meta.get('last_row_id')
        self.rowcount = self.meta.get('changes', -1)
        self._rows = result.get('results') or []
        self._index = 0
        return self

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        if self._index == 0:
            return list(self._rows)
        remaining = self._rows[self._index :]
        self._index = len(self._rows)
        return list(remaining)


class D1Connection:
    def __init__(self, account_id, database_id, api_token, api_base_url='https://api.cloudflare.com/client/v4', timeout_seconds=30):
        self._account_id = (account_id or '').strip()
        self._database_id = (database_id or '').strip()
        self._api_token = (api_token or '').strip()
        self._api_base_url = (api_base_url or '').rstrip('/')
        self._timeout_seconds = int(timeout_seconds or 30)

        if not self._account_id or not self._database_id or not self._api_token:
            raise D1Error('Credenciais do Cloudflare D1 incompletas.')

    @classmethod
    def from_env(cls):
        return cls(
            account_id=os.getenv('CLOUDFLARE_ACCOUNT_ID', ''),
            database_id=os.getenv('CLOUDFLARE_D1_DATABASE_ID', ''),
            api_token=os.getenv('CLOUDFLARE_API_TOKEN', ''),
            api_base_url=os.getenv('CLOUDFLARE_API_BASE_URL', 'https://api.cloudflare.com/client/v4'),
            timeout_seconds=os.getenv('CLOUDFLARE_D1_TIMEOUT_SECONDS', '30')
        )

    def cursor(self):
        return D1Cursor(self)

    def execute(self, sql, params=()):
        cursor = self.cursor()
        return cursor.execute(sql, params=params)

    def executemany(self, sql, seq_of_params):
        cursor = self.cursor()
        last_cursor = None
        for params in seq_of_params or []:
            last_cursor = cursor.execute(sql, params=params)
        return last_cursor or cursor

    def commit(self):
        return None

    def close(self):
        return None

    def _query(self, sql, params=None):
        url = f'{self._api_base_url}/accounts/{self._account_id}/d1/database/{self._database_id}/query'
        payload = {
            'sql': sql,
            'params': list(params or [])
        }
        headers = {
            'Authorization': f'Bearer {self._api_token}',
            'Content-Type': 'application/json'
        }

        response = requests.post(url, json=payload, headers=headers, timeout=self._timeout_seconds)
        try:
            data = response.json()
        except ValueError as exc:
            raise D1Error('Resposta inválida do Cloudflare D1.') from exc

        if not response.ok or not data.get('success', False):
            errors = data.get('errors') or []
            message = errors[0].get('message') if errors else 'Falha ao consultar Cloudflare D1.'
            lowered = (message or '').lower()
            if 'unique constraint' in lowered or 'foreign key constraint' in lowered or 'constraint failed' in lowered:
                raise sqlite3.IntegrityError(message)
            raise sqlite3.OperationalError(message)

        result_list = data.get('result') or []
        if not result_list:
            return {'results': [], 'meta': {}}

        first = result_list[0] or {}
        if not first.get('success', False):
            message = 'Falha ao executar SQL no Cloudflare D1.'
            raise sqlite3.OperationalError(message)

        return {
            'results': first.get('results') or [],
            'meta': first.get('meta') or {}
        }
