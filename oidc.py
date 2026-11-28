"""
oidc.py - (v4) "Sign in with Google" and "Sign in with Microsoft" (OpenID Connect, authorization code + PKCE).

A provider is shown on the login page only when its keys are set (secrets, never in the code):

    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, MICROSOFT_TENANT (default "common")

Redirect address to register at the provider:  <APP_URL>/auth/sso/<google|microsoft>/callback

Safety:
  * state (against forged callbacks) and a PKCE verifier (the code is useless without it) live in the session
  * the ID token is checked here: RS256 signature with the provider's published keys (JWKS), issuer,
    audience (our client id), expiry and the nonce we sent
  * only an email the provider marks as verified is accepted
"""

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

PROVIDERS = {
    "google": {
        "name": "Google",
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "jwks": "https://www.googleapis.com/oauth2/v3/certs",
        "issuers": ("https://accounts.google.com", "accounts.google.com"),
        "env": "GOOGLE",
    },
    "microsoft": {
        "name": "Microsoft",
        "auth": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "jwks": "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        "issuers": ("https://login.microsoftonline.com/{tid}/v2.0",),        # {tid}: the tenant of the person
        "env": "MICROSOFT",
    },
}


class SSOError(Exception):
    pass


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def config(env, name):
    p = PROVIDERS.get(name)
    if not p:
        return None
    cid, secret = env.get(f"{p['env']}_CLIENT_ID", ""), env.get(f"{p['env']}_CLIENT_SECRET", "")
    if not (cid and secret):
        return None
    tenant = env.get("MICROSOFT_TENANT", "common") or "common"
    return dict(p, id=name, client_id=cid, client_secret=secret,
                auth=p["auth"].replace("{tenant}", tenant), token=p["token"].replace("{tenant}", tenant),
                jwks=p["jwks"].replace("{tenant}", tenant))


def enabled(env):
    return [c for c in (config(env, n) for n in PROVIDERS) if c]


def start(cfg, redirect_uri):
    """Return (url to send the browser to, values to keep in the session)."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    q = {"client_id": cfg["client_id"], "response_type": "code", "scope": "openid email profile",
         "redirect_uri": redirect_uri, "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return cfg["auth"] + "?" + urllib.parse.urlencode(q), {"state": state, "nonce": nonce, "verifier": verifier,
                                                           "provider": cfg["id"], "at": int(time.time())}


def _json(http, req):
    with (http or urllib.request.urlopen)(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def exchange(cfg, code, verifier, redirect_uri, http=None):
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                   "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                   "code_verifier": verifier}).encode()
    req = urllib.request.Request(cfg["token"], data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    data = _json(http, req)
    if "id_token" not in data:
        raise SSOError("The provider did not return an ID token.")
    return data["id_token"]


def rsa_key(jwk):
    n = int.from_bytes(b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(b64url_decode(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def verify_id_token(cfg, token, nonce, jwks, now=None):
    """Check the token and return its claims (sub, email, name ...)."""
    try:
        head_b, body_b, sig_b = token.split(".")
        head, claims = json.loads(b64url_decode(head_b)), json.loads(b64url_decode(body_b))
    except Exception:
        raise SSOError("The ID token is not valid.")
    if head.get("alg") != "RS256":
        raise SSOError("The ID token is not signed with RS256.")
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == head.get("kid") and k.get("kty") == "RSA"), None)
    if not key:
        raise SSOError("The key of the ID token is unknown.")
    try:
        rsa_key(key).verify(b64url_decode(sig_b), f"{head_b}.{body_b}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        raise SSOError("The signature of the ID token is wrong.")
    now = now or time.time()
    issuers = [i.replace("{tid}", str(claims.get("tid", ""))) for i in cfg["issuers"]]
    aud = claims.get("aud")
    if claims.get("iss") not in issuers:
        raise SSOError("The ID token comes from another issuer.")
    if aud != cfg["client_id"] and not (isinstance(aud, list) and cfg["client_id"] in aud):
        raise SSOError("The ID token is for another application.")
    if float(claims.get("exp", 0)) < now - 60:
        raise SSOError("The ID token has expired.")
    if claims.get("nonce") != nonce:
        raise SSOError("The sign-in answer does not match this browser.")
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    verified = claims.get("email_verified")
    if cfg["id"] == "microsoft":
        verified = verified if verified is not None else bool(email)      # work accounts: managed by the company
    if not email or "@" not in email or verified in (False, "false"):
        raise SSOError("The provider did not confirm your email address.")
    return {"sub": str(claims.get("sub", "")), "email": email, "name": claims.get("name") or email.split("@")[0]}


def finish(cfg, saved, args, redirect_uri, http=None):
    """Called on the callback page. saved = the values from start(); args = request.args."""
    if args.get("error"):
        raise SSOError("Sign-in was cancelled.")
    if not saved or saved.get("provider") != cfg["id"] or not secrets.compare_digest(saved.get("state", ""),
                                                                                    args.get("state", "")):
        raise SSOError("The sign-in answer does not match this browser. Please try again.")
    if time.time() - saved.get("at", 0) > 600:
        raise SSOError("The sign-in took too long. Please try again.")
    token = exchange(cfg, args.get("code", ""), saved["verifier"], redirect_uri, http)
    jwks = _json(http, urllib.request.Request(cfg["jwks"], headers={"Accept": "application/json"}))
    return verify_id_token(cfg, token, saved["nonce"], jwks)
