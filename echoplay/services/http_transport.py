"""Reusable HTTP connections for consecutive byte ranges (one downloader thread)."""
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import getproxies, proxy_bypass
import base64
import http.client


class PersistentRangeClient:
    def __init__(self):
        self._connection = None
        self._origin = None

    def close(self):
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._origin = None

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        origin = (url.scheme, url.hostname, url.port)
        if origin != self._origin:
            self.close()
        if self._connection is None:
            proxy_url = None if proxy_bypass(url.hostname) else getproxies().get(url.scheme)
            if proxy_url:
                proxy = urlsplit(proxy_url if "://" in proxy_url else "http://" + proxy_url)
                if proxy.scheme != "http":
                    # Preserve urllib's behavior for unusual proxy configurations.
                    from urllib.request import urlopen
                    return urlopen(request, timeout=timeout)
                cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
                connection = cls(proxy.hostname, proxy.port or 80, timeout=timeout)
                if url.scheme == "https":
                    tunnel_headers = {}
                    if proxy.username:
                        credentials = f"{unquote(proxy.username)}:{unquote(proxy.password or '')}"
                        token = base64.b64encode(credentials.encode()).decode()
                        tunnel_headers["Proxy-Authorization"] = "Basic " + token
                    connection.set_tunnel(url.hostname, url.port or 443, tunnel_headers)
                self._absolute_target = url.scheme == "http"
            else:
                cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
                connection = cls(url.hostname, url.port, timeout=timeout)
                self._absolute_target = False
            self._connection = connection
            self._origin = origin
        target = request.full_url if self._absolute_target else (url.path or "/") + (
            "?" + url.query if url.query else "")
        try:
            self._connection.request("GET", target, headers=dict(request.header_items()))
            response = self._connection.getresponse()
            if response.status >= 300:
                response.close()
                self.close()
                # Redirects retain urllib's validation and redirect handling.
                if response.status < 400:
                    from urllib.request import urlopen
                    return urlopen(request, timeout=timeout)
                raise HTTPError(request.full_url, response.status, response.reason,
                                response.headers, None)
            return response
        except Exception:
            self.close()
            raise
