"""Bounded HTTP capture: local complete-response time, never provider receipt time."""
import time
import urllib.error
import urllib.parse
import urllib.request
from .contracts import now, iso, digest


class Fetcher:
    def __init__(self, config, opener=None):
        self.config = config
        self.opener = opener or urllib.request.urlopen

    def get(self, source, endpoint, params):
        started = now()
        url = endpoint + '?' + urllib.parse.urlencode(params)
        body = b''
        status = None
        headers = {}
        error = None
        complete = False
        tick = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'ZUUU-T0-Experimental-V1/1.0'})
            with self.opener(request, timeout=self.config['http_timeout_seconds']) as response:
                status = response.status
                headers = dict(response.headers)
                limit = self.config['http_max_bytes']
                chunks = []
                size = 0
                while True:
                    chunk = response.read(min(65536, limit - size + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    body = b''.join(chunks)
                    if size > limit:
                        raise ValueError('RESPONSE_TOO_LARGE')
                    if time.monotonic() - tick > self.config['http_timeout_seconds'] * 2:
                        raise TimeoutError('TOTAL_DOWNLOAD_TIMEOUT')
                length = response.headers.get('Content-Length')
                if length is not None and int(length) != len(body):
                    raise ValueError('PARTIAL_HTTP_BODY')
                complete = 200 <= status < 300
                if not complete:
                    error = 'HTTP_' + str(status)
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = dict(exc.headers or {})
            body = exc.read(self.config['http_max_bytes'])
            error = 'HTTP_' + str(status)
        except Exception as exc:
            partial = getattr(exc, 'partial', None)
            if isinstance(partial, bytes):
                body += partial
            error = type(exc).__name__ + ': ' + str(exc)
        finished = now()  # Obtained immediately after complete read or terminal failure.
        return dict(source=source, endpoint=endpoint, request_params=params,
                    download_start_time=iso(started), download_complete_time=iso(finished),
                    body_complete=complete, http_status=status, response_headers=headers,
                    payload_sha256=digest(body), content_hex=body.hex(), error=error)
