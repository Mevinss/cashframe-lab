"""Export an existing credential only to the ignored private handoff directory."""
from datetime import datetime, timezone
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

import httpx

from .jury_delivery import read_existing_key, validate_base_url
from .launcher import load_environment


def validate_access_root(value):
    """Explicit HTTPS origin or loopback HTTP; no credentials or query secrets."""
    value = validate_base_url(value)
    parsed = urlsplit(value)
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost':
        return value
    valid = parsed.scheme == 'https'
    try:
        address = ipaddress.ip_address(host)
        valid = address.is_loopback or (valid and address.is_global)
    except ValueError:
        labels = host.split('.')
        valid = (valid and len(labels) >= 2
                 and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
                 and not host.endswith(('.localhost', '.local', '.invalid', '.test', '.example'))
                 and host not in ('example.com', 'example.net', 'example.org'))
    if not valid:
        raise ValueError('Use HTTPS for remote origins; HTTP is allowed only on loopback, without credentials or query.')
    return value


def _selected_key(root, key_file):
    if key_file is None:
        key = read_existing_key(root, load_environment(root))
    else:
        try:
            supplied = Path(key_file).read_text(encoding='utf-8-sig').strip()
        except OSError:
            raise ValueError('Select an existing key file; no credential is generated.') from None
        if not supplied:
            raise ValueError('The selected key file is empty.')
        key = read_existing_key(root, {'CASHFRAME_API_KEY': supplied})
    if not key or len(key) > 4096 or any(ord(char) < 32 or ord(char) > 126 for char in key):
        raise ValueError('The selected credential must be a single printable ASCII header value.')
    return key


def _probe(base_url, key, transport, timeout):
    outcome = {'http_status': None, 'verified': False, 'status': 'unverified'}
    try:
        with httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                          follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', '/api/v1/system', headers={'X-API-Key': key}) as response:
                outcome['http_status'] = response.status_code
                if response.status_code == 401:
                    outcome['status'] = 'rejected'
                    return outcome
                if response.status_code != 200:
                    return outcome
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if len(body) + len(chunk) > 65536:
                        return outcome
                    body.extend(chunk)
                data = json.loads(body)
                if isinstance(data, dict) and data.get('ready') is True:
                    outcome.update(verified=True, status='verified')
    except (httpx.HTTPError, ValueError, UnicodeError):
        pass
    return outcome


def export_jury_access(root, base_url, *, key_file=None, transport=None, timeout=15):
    """Write local/jury-access.md without changing a key or claiming failed access.

    Only one bounded GET /api/v1/system is made to the explicitly selected origin
    (HTTPS except loopback). No redirects, proxy environment, uploads, jobs or media are read.
    The returned summary contains neither credential nor server response body.
    """
    root = Path(root).resolve()
    base_url = validate_access_root(base_url)
    key = _selected_key(root, key_file)
    directory = root / 'local'
    if directory.exists() and directory.resolve() != directory:
        raise ValueError('The private handoff directory must stay inside this installation.')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'jury-access.md'
    if target.is_symlink():
        raise ValueError('The private handoff cannot replace a symbolic link.')
    observed = _probe(base_url, key, transport, timeout)
    status_text = {
        'verified': 'Ключ проверен: сервер принял его, API технически готов.',
        'rejected': 'Ключ не принят этим сервером (HTTP 401). Доступ по нему не работает.',
        'unverified': 'Доступ не подтверждён: сервер недоступен, вернул иной ответ или не готов.',
    }[observed['status']]
    checked = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    content = (
        '# Доступ для жюри — приватная памятка\n\n'
        'Файл содержит ключ. Передавайте только команде и жюри; в Git он исключён.\n\n'
        f'Сайт: {base_url}/\n\n'
        f'API: {base_url}/api/v1/\n\n'
        f'Проверка: {checked}. **{status_text}**\n\n'
        '## Выбранный ключ\n\n'
        f'<pre><code>{html.escape(key)}</code></pre>\n\n'
        'В интерфейсе вставьте ключ в поле доступа и нажмите «Подключить». '
        'Для API передавайте то же значение в заголовке `X-API-Key`. '
        'Ключ нельзя добавлять к URL.\n\n'
        'Загрузка: `POST /api/v1/analyze`, поле файла `file`. '
        'Результат: `GET /api/v1/jobs/{job_id}`.\n\n'
        '## Если ключ не принят или доступ не подтверждён\n\n'
        'Администратору выбранного сервера нужно приватно согласовать этот ключ с '
        '`CASHFRAME_API_KEY` в окружении сервера и перезапустить сервис. '
        'Скрипт не меняет ключ, окружение или удалённый сервер. '
        'После настройки повторно экспортируйте памятку и проверьте авторизацию.\n\n'
        'Принятый ключ подтверждает технический доступ в момент проверки; '
        'он не подтверждает совпадение весов, качество модели или успешную обработку видео.\n'
    )
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='.jury-access-', suffix='.tmp', delete=False) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return {'path': str(target), **observed}
