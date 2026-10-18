"""Secret-free handoff checks; never process or fetch private training records.

Technical readiness, external reachability and model quality are deliberately
independent. A successful local check cannot certify the latter two.
"""
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx


def validate_base_url(value):
    """Only root HTTP(S) URLs; reject credentials, query secrets and redirects."""
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        raise ValueError('Expected a root HTTP(S) service URL without credentials or query.')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and parsed.path in ('', '/')
                 and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Expected a root HTTP(S) service URL without credentials or query.')
    return value.rstrip('/')


def _public_https_candidate(url):
    parsed = urlsplit(url)
    host = parsed.hostname.lower().rstrip('.')
    if parsed.scheme != 'https' or host == 'localhost' or host.endswith(('.localhost', '.local', '.invalid', '.test', '.example')):
        return False
    if host in ('example.com', 'example.net', 'example.org') or any(host.endswith('.' + domain) for domain in ('example.com', 'example.net', 'example.org')):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return '.' in host


def read_existing_key(root, environment):
    """Read the effective installation key without creating or rotating it."""
    supplied = environment.get('CASHFRAME_API_KEY', '').strip()
    if supplied:
        return supplied
    target = Path(root) / 'local/api-key.txt'
    try:
        key = target.read_text(encoding='utf-8').strip()
    except OSError:
        raise ValueError('No existing installation key. Start the service first.') from None
    if not key:
        raise ValueError('The existing installation key is empty.')
    return key


def probe_service(base_url, key, *, transport=None, timeout=65):
    """GET-only probes with independent header authentication and no cookies.

    No response bodies, labels, keys, exception messages or private records are
    copied to the report. Redirects are refused to avoid forwarding a key.
    """
    base_url = validate_base_url(base_url)
    observations = {'checks': {}, 'http_status': {}, 'model': {
        'profile': None, 'type_classifier_status': 'unverified', 'class_count': 0,
    }}
    paths = (
        ('ui_available', '/', None),
        ('runtime_ready', '/health/ready', None),
        ('anonymous_rejected', '/api/v1/system', None),
        ('wrong_key_rejected', '/api/v1/system', 'jury-preflight-deliberately-wrong-key'),
        ('header_auth_accepted', '/api/v1/system', key),
        ('private_review_disabled', '/dataset-review', None),
    )
    with httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                      trust_env=False, follow_redirects=False) as client:
        for name, path, supplied_key in paths:
            # A server may issue cookies on any response. Never use one to make
            # the jury's explicit header check accidentally pass.
            client.cookies.clear()
            try:
                response = client.get(path, headers={'X-API-Key': supplied_key} if supplied_key is not None else {})
            except httpx.HTTPError:
                observations['http_status'][name] = None
                observations['checks'][name] = False
                continue
            observations['http_status'][name] = response.status_code
            expected = 401 if name in ('anonymous_rejected', 'wrong_key_rejected') else 404 if name == 'private_review_disabled' else 200
            passed = response.status_code == expected
            if name in ('runtime_ready', 'header_auth_accepted') and passed:
                try:
                    body = response.json()
                    passed = isinstance(body, dict) and body.get('ready') is True
                except ValueError:
                    body, passed = {}, False
                if name == 'header_auth_accepted' and passed:
                    profile = body.get('model_profile')
                    if isinstance(profile, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', profile):
                        observations['model']['profile'] = profile
                    classifier = body.get('type_classifier', {})
                    if isinstance(classifier, dict):
                        status = classifier.get('status')
                        if status in ('absent', 'inactive', 'active', 'invalid'):
                            observations['model']['type_classifier_status'] = status
                        count = classifier.get('class_count')
                        if type(count) is int and 0 <= count <= 10000:
                            observations['model']['class_count'] = count
            observations['checks'][name] = passed
    return observations


def build_manifest(base_url, observations):
    """A deterministic summary of observed facts, never a certification."""
    base_url = validate_base_url(base_url)
    checks = dict(observations['checks'])
    public = _public_https_candidate(base_url)
    blockers = []
    if not public:
        blockers.append('public_https_url_missing')
    if not checks.get('private_review_disabled'):
        blockers.append('private_review_enabled_or_unverified')
    if not all(checks.get(name) for name in ('ui_available', 'runtime_ready', 'anonymous_rejected', 'wrong_key_rejected', 'header_auth_accepted')):
        blockers.append('technical_preflight_failed')
    # A request from the server's own host is not independent proof of public
    # access or a <5-minute successful MOV analysis.
    blockers.append('external_client_mov_smoke_not_recorded')
    model = dict(observations['model'])
    gaps = ['independent_detection_accuracy_not_certified',
            'event_localization_and_evidence_relevance_not_certified',
            'new_camera_angle_not_certified', 'automatic_legal_applicability_not_certified']
    if model.get('type_classifier_status') != 'active':
        gaps.insert(0, 'accepted_type_classifier_missing')
    return {
        'schema': 'jury-delivery-preflight-v1', 'service_url': base_url,
        'public_url_candidate': base_url if public else None,
        'public_reachability_verified': False,
        'technical_preflight_passed': all(checks.values()) and len(checks) == 6,
        'submission_complete': False,
        'checks': checks, 'http_status': dict(observations['http_status']),
        'model': model, 'submission_blockers': blockers, 'quality_gaps': gaps,
        'jury_api': {'upload': 'POST /api/v1/analyze', 'field': 'file',
                     'result': 'GET /api/v1/jobs/{job_id}', 'header': 'X-API-Key',
                     'verdicts': ['no_violation', 'violation'], 'deadline_sec': 300},
        'locality': {'processing_location': 'installed local worker',
                     'offline_inference_enforced_by': 'cashframe.pipeline.block_outgoing_network',
                     'independent_offline_smoke_recorded': False},
        'handoff': {'private_key_file': 'local/jury-access.txt',
                    'architecture': 'docs/architecture.md', 'rules': 'docs/rules.md',
                    'types_and_limitations': 'docs/jury-access-and-classification.md',
                    'launch': 'docs/team-setup.md', 'deployment': 'docs/deployment.md'},
    }
