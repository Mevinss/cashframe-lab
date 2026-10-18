"""Compare public deployment assets without reading uploads, jobs or annotations."""
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

import httpx

from .jury_delivery import validate_base_url


PUBLIC_PATHS = (
    '/', '/static/app.js', '/static/styles.css', '/static/hero.js',
    '/static/i18n.js', '/static/i18n.ru.js', '/static/translations.js',
    '/static/annotation-templates.js', '/static/brand/favicon.svg',
)


def _checked_url(value):
    value = validate_base_url(value)
    parsed = urlsplit(value)
    local = parsed.hostname == 'localhost'
    try:
        local = local or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != 'https' and not local:
        raise ValueError('Remote deployment checks require HTTPS; HTTP is allowed only for loopback.')
    return value


def _get(client, path, *, key=None, max_bytes):
    """Bound response reads; discard body/error details from returned metadata."""
    client.cookies.clear()
    summary = {'http_status': None, 'sha256': None, 'bytes': None, 'error': None}
    try:
        with client.stream('GET', path, headers={'X-API-Key': key} if key else {}) as response:
            summary['http_status'] = response.status_code
            if response.status_code != 200:
                return summary, None
            payload = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(payload) + len(chunk) > max_bytes:
                    summary['error'] = 'response_too_large'
                    return summary, None
                payload.extend(chunk)
            body = bytes(payload)
            summary.update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
            return summary, body
    except httpx.HTTPError:
        summary['error'] = 'request_failed'
        return summary, None


def _json_object(body):
    try:
        result = json.loads(body) if body is not None else None
    except (ValueError, UnicodeError):
        result = None
    return result if isinstance(result, dict) else {}


def compare_deployments(reference_url, target_url, *, key=None, transport=None,
                        timeout=10, max_bytes=2 * 1024 * 1024):
    """GET-only parity report. Matching UI/profile cannot prove matching weights.

    Optional key checks use only /api/v1/system on the two explicitly supplied
    origins. Cookies/proxies/redirects are disabled and no private fields survive.
    """
    reference_url, target_url = _checked_url(reference_url), _checked_url(target_url)
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError('max_bytes must be a positive integer.')
    observations = {}
    for name, url in (('reference', reference_url), ('target', target_url)):
        observed = {'assets': {}, 'ready': False, 'schema': {}, 'auth': None}
        with httpx.Client(base_url=url, timeout=timeout, transport=transport,
                          follow_redirects=False, trust_env=False) as client:
            for path in PUBLIC_PATHS:
                observed['assets'][path], _ = _get(client, path, max_bytes=max_bytes)
            ready_meta, ready_body = _get(client, '/health/ready', max_bytes=max_bytes)
            observed['ready'] = (ready_meta['http_status'] == 200
                                 and _json_object(ready_body).get('ready') is True)
            schema_meta, schema_body = _get(client, '/openapi.json', max_bytes=max_bytes)
            schema = _json_object(schema_body)
            # Compare generic contract data only; do not print OpenAPI contents.
            if isinstance(schema.get('paths'), dict) and isinstance(schema.get('openapi'), str):
                canonical = json.dumps(schema, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
                schema_meta['canonical_sha256'] = hashlib.sha256(canonical).hexdigest()
            else:
                schema_meta['canonical_sha256'] = None
            observed['schema'] = schema_meta
            if key:
                meta, body = _get(client, '/api/v1/system', key=key, max_bytes=max_bytes)
                system = _json_object(body)
                profile = system.get('model_profile')
                observed['auth'] = {
                    'http_status': meta['http_status'],
                    'accepted': meta['http_status'] == 200 and system.get('ready') is True,
                    'model_profile': profile if isinstance(profile, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', profile) else None,
                }
        observations[name] = observed
    first, second = observations['reference'], observations['target']
    assets = {path: {'reference': first['assets'][path], 'target': second['assets'][path],
                     'matches': first['assets'][path]['sha256'] is not None
                     and first['assets'][path]['sha256'] == second['assets'][path]['sha256']}
              for path in PUBLIC_PATHS}
    first_schema, second_schema = first['schema']['canonical_sha256'], second['schema']['canonical_sha256']
    return {
        'schema': 'deployment-parity-v1', 'reference_url': reference_url, 'target_url': target_url,
        'frontend_matches': all(asset['matches'] for asset in assets.values()),
        'assets': assets, 'both_ready': first['ready'] and second['ready'],
        'readiness': {'reference': first['ready'], 'target': second['ready']},
        'api_schema_matches': first_schema is not None and first_schema == second_schema,
        'api_schema': {'reference': first['schema'], 'target': second['schema']},
        'auth': {'checked': bool(key), 'reference': first['auth'], 'target': second['auth']},
        'runtime_weights_identity_verified': False,
        'limitations': ['matching_public_assets_does_not_prove_runtime_or_weight_identity',
                        'no_video_processing_or_quality_test_performed',
                        'remote_host_not_updated_by_this_read_only_check'],
    }
