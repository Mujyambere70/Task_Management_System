"""
s3.py - (v4) keep task files in S3-compatible object storage instead of the database.

Works with Amazon S3, Cloudflare R2, Backblaze B2, MinIO, Tigris on Fly.io ... anything that speaks the S3 API.
Switch it on with environment variables (secrets, never in the code):

    S3_BUCKET       name of the bucket                         (required)
    S3_ENDPOINT     https://<account>.r2.cloudflarestorage.com  (empty = Amazon: https://s3.<region>.amazonaws.com)
    S3_REGION       us-east-1 / auto / ...                      (default us-east-1)
    S3_ACCESS_KEY, S3_SECRET_KEY

Requests are signed with AWS Signature Version 4 (written here with hashlib/hmac, no boto3 needed).
Without S3_BUCKET the files stay in the database as in version 3.
"""

import hashlib
import hmac
import urllib.parse
import urllib.request
from datetime import datetime, timezone

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def _quote(text, safe="-_.~"):
    return urllib.parse.quote(text, safe=safe)


def canonical_query(query):
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    return "&".join(f"{_quote(k)}={_quote(v)}" for k, v in sorted(pairs))


def signature_v4(method, url, headers, payload_hash, access_key, secret_key, region, service, amz_date):
    """Return the Authorization header value for one request (AWS Signature Version 4).
    headers: dict of the headers that are signed (host is added from the url when missing)."""
    u = urllib.parse.urlsplit(url)
    signed = {k.lower().strip(): " ".join(str(v).split()) for k, v in headers.items()}
    signed.setdefault("host", u.netloc)
    names = sorted(signed)
    canonical = "\n".join([
        method.upper(),
        _quote(u.path or "/", safe="/-_.~"),
        canonical_query(u.query),
        "".join(f"{n}:{signed[n]}\n" for n in names),
        ";".join(names),
        payload_hash,
    ])
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"


class ObjectStore:
    """put / get / delete of one object. `http` is urllib.request.urlopen, or a fake in the tests."""

    def __init__(self, env, http=None):
        self.bucket = env.get("S3_BUCKET", "").strip()
        self.region = env.get("S3_REGION", "").strip() or "us-east-1"
        endpoint = env.get("S3_ENDPOINT", "").strip().rstrip("/")
        self.endpoint = endpoint or f"https://s3.{self.region}.amazonaws.com"
        self.key, self.secret = env.get("S3_ACCESS_KEY", ""), env.get("S3_SECRET_KEY", "")
        self.http = http

    @property
    def enabled(self):
        return bool(self.bucket and self.key and self.secret and self.endpoint.startswith("https://"))

    def url(self, key):
        return f"{self.endpoint}/{self.bucket}/{_quote(key, safe='/-_.~')}"           # path-style works everywhere

    def _request(self, method, key, data=b"", content_type=None):
        url = self.url(key)
        amz = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        payload = hashlib.sha256(data).hexdigest() if data else EMPTY_SHA256
        headers = {"x-amz-date": amz, "x-amz-content-sha256": payload}
        if content_type:
            headers["content-type"] = content_type
        auth = signature_v4(method, url, headers, payload, self.key, self.secret, self.region, "s3", amz)
        req = urllib.request.Request(url, data=data if method == "PUT" else None, method=method,
                                     headers=dict(headers, Authorization=auth))
        with (self.http or urllib.request.urlopen)(req, timeout=20) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"storage answered {resp.status}")
            return resp.read() if method == "GET" else b""

    def put(self, key, data, content_type="application/octet-stream"):
        self._request("PUT", key, data, content_type)

    def get(self, key):
        return self._request("GET", key)

    def delete(self, key):
        self._request("DELETE", key)
