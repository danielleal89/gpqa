import mimetypes
import os

from flask import Response, abort, url_for


R2_PREFIX = 'r2:'
_r2_client = None


class StorageClientError(Exception):
    pass


def _clean_env(value):
    return (value or '').strip().strip('`').strip()


def is_r2_enabled():
    return all(
        _clean_env(os.getenv(env_name))
        for env_name in (
            'R2_BUCKET_NAME',
            'R2_ACCESS_KEY_ID',
            'R2_SECRET_ACCESS_KEY',
            'R2_ENDPOINT',
        )
    )


def get_r2_bucket_name():
    return _clean_env(os.getenv('R2_BUCKET_NAME'))


def get_r2_client():
    global _r2_client
    if _r2_client is None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StorageClientError('boto3 nao esta instalado no ambiente atual.') from exc
        _r2_client = boto3.client(
            's3',
            endpoint_url=_clean_env(os.getenv('R2_ENDPOINT')),
            aws_access_key_id=_clean_env(os.getenv('R2_ACCESS_KEY_ID')),
            aws_secret_access_key=_clean_env(os.getenv('R2_SECRET_ACCESS_KEY')),
            region_name='auto',
            config=Config(signature_version='s3v4')
        )
    return _r2_client


def save_upload(file_storage, object_key, local_relative_path):
    if not is_r2_enabled():
        raise StorageClientError('R2 nao esta configurado no ambiente atual.')

    file_storage.stream.seek(0)
    extra_args = {}
    if file_storage.mimetype:
        extra_args['ContentType'] = file_storage.mimetype
    upload_kwargs = {}
    if extra_args:
        upload_kwargs['ExtraArgs'] = extra_args
    get_r2_client().upload_fileobj(
        file_storage.stream,
        get_r2_bucket_name(),
        object_key,
        **upload_kwargs
    )
    return f'{R2_PREFIX}{object_key}'


def delete_file(stored_path):
    clean_path = (stored_path or '').strip()
    if not clean_path:
        return

    if clean_path.startswith(R2_PREFIX):
        object_key = clean_path[len(R2_PREFIX):]
        try:
            get_r2_client().delete_object(Bucket=get_r2_bucket_name(), Key=object_key)
        except Exception:
            return
        return

def build_file_url(stored_path):
    clean_path = (stored_path or '').strip()
    if not clean_path:
        return ''

    public_base_url = _clean_env(os.getenv('R2_PUBLIC_BASE_URL'))
    if clean_path.startswith(R2_PREFIX) and public_base_url:
        object_key = clean_path[len(R2_PREFIX):]
        return f"{public_base_url.rstrip('/')}/{object_key}"

    return url_for('storage_file', path=clean_path)


def serve_file(stored_path):
    clean_path = (stored_path or '').strip()
    if not clean_path:
        abort(404)

    if clean_path.startswith(R2_PREFIX):
        object_key = clean_path[len(R2_PREFIX):]
        try:
            obj = get_r2_client().get_object(Bucket=get_r2_bucket_name(), Key=object_key)
        except Exception:
            abort(404)

        content_type = obj.get('ContentType') or mimetypes.guess_type(object_key)[0] or 'application/octet-stream'
        return Response(
            obj['Body'].read(),
            mimetype=content_type,
            headers={'Content-Disposition': f'inline; filename="{os.path.basename(object_key)}"'}
        )

    abort(404)
