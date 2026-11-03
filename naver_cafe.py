"""Naver Cafe authentication and public multipart posting."""
import html
import json
import os
import re
import uuid
import time
from urllib.error import HTTPError
from urllib.parse import quote_plus, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler




class CafeError(Exception):
    pass


def safe_error_description(value, headers):
    if not isinstance(value, str):
        return 'Description unavailable'
    secrets = [os.environ.get(k, '') for k in ('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET',
               'NAVER_REFRESH_TOKEN', 'KOPIS_API_KEY', 'DISCORD_WEBHOOK_URL')]
    bearer = (headers or {}).get('Authorization', '')
    secrets += [bearer, bearer.removeprefix('Bearer ')]
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        for variant in (secret, quote_plus(secret)):
            value = value.replace(variant, '[REDACTED]')
    value = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+', '[REDACTED]', value)
    value = re.sub(r'[A-Za-z0-9_+/=.%~-]{20,}', '[REDACTED]', value)
    return ' '.join(value.split())[:600]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise CafeError('Naver request redirect refused; no credentials logged.')


def transport(url, data=None, headers=None, limit=6_000_000):
    try:
        req = Request(url, data=data, headers=headers or {})
        with build_opener(NoRedirect).open(req, timeout=30) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise CafeError('Response exceeds allowed size.')
            return body
    except HTTPError as exc:
        code = 'unrecognized'
        description = 'Description unavailable'
        if urlsplit(url).hostname == 'openapi.naver.com':
            try:
                value = json.loads(exc.read(20000))
                message = value.get('message', {})
                error = message.get('error', {}) if isinstance(message, dict) else {}
                candidate = value.get('errorCode', value.get('error_code'))
                if candidate is None and isinstance(error, dict):
                    candidate = error.get('code')
                if isinstance(candidate, (str, int)) and re.fullmatch(r'[A-Z]{0,3}[0-9]{1,5}', str(candidate)):
                    code = str(candidate)
                detail = value.get('errorMessage', value.get('error_description'))
                if detail is None and isinstance(error, dict):
                    detail = error.get('msg', error.get('message'))
                description = safe_error_description(detail, headers)
            except Exception:
                pass
        raise CafeError(f'Naver/image HTTP {exc.code}; API code {code}; detail: {description}') from None
    except CafeError:
        raise
    except Exception:
        raise CafeError('Naver/image request failed; details suppressed.') from None


def access_token():
    names = ('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET', 'NAVER_REFRESH_TOKEN')
    if any(not os.environ.get(n) for n in names):
        raise CafeError('Add NAVER_CLIENT_ID, NAVER_CLIENT_SECRET and NAVER_REFRESH_TOKEN to Actions Secrets.')
    data = urlencode({'grant_type': 'refresh_token', 'client_id': os.environ[names[0]],
                      'client_secret': os.environ[names[1]], 'refresh_token': os.environ[names[2]]}).encode()
    value = json.loads(transport('https://nid.naver.com/oauth2.0/token', data,
                                {'Content-Type': 'application/x-www-form-urlencoded'}))
    if not value.get('access_token') or value.get('error'):
        raise CafeError('Naver login refresh failed. Reconnect the posting account.')
    return value['access_token']


def multipart(draft, poster):
    boundary = 'concert-' + uuid.uuid4().hex
    fields = {'subject': quote_plus(draft['subject']), 'content': quote_plus(draft['content']),
              'openyn': 'true', 'searchopen': 'true', 'ccl': 'false'}
    body = b''
    for name, value in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                 f'Content-Type: text/plain; charset=UTF-8\r\n\r\n{value}\r\n').encode()
    if poster:
        filename, mime, data = poster
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
                 f'Content-Type: {mime}\r\n\r\n').encode() + data + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    return body, 'multipart/form-data; boundary=' + boundary


def publish(draft, poster, token, config):
    club, menu = str(config['club_id']), str(config['menu_id'])
    if not club.isdigit() or not menu.isdigit():
        raise CafeError('Invalid cafe or board ID.')
    body, content_type = multipart(draft, poster)
    result = json.loads(transport(f'https://openapi.naver.com/v1/cafe/{club}/menu/{menu}/articles', body,
                                  {'Authorization': 'Bearer ' + token, 'Content-Type': content_type}))
    message = result.get('message', result)
    if str(message.get('status')) != '200':
        raise CafeError('Naver did not confirm article creation. Review pending article before retrying.')
    value = message.get('result', message)
    article_id = str(value.get('articleId', ''))
    if not article_id.isdigit():
        raise CafeError('Article creation response missing ID; review pending article.')
    return {'article_id': article_id,
            'url': f'https://cafe.naver.com/f-e/cafes/{club}/articles/{article_id}',
            'announced': False}



