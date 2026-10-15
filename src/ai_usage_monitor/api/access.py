"""Verify Cloudflare Access at the origin; never trust identity headers alone."""

import threading

import jwt


class AccessVerifier:
    def __init__(self, settings):
        self.issuer = "https://" + settings.mcp_access_team
        self.audience = settings.mcp_access_aud
        self.emails = {email.lower() for email in settings.mcp_access_emails}
        self.keys = jwt.PyJWKClient(
            self.issuer + "/cdn-cgi/access/certs", timeout=5, lifespan=300
        )
        self.lock = threading.Lock()

    def verify(self, token: str) -> bool:
        if not token or len(token) > 16384 or not self.audience or not self.emails:
            return False
        try:
            if jwt.get_unverified_header(token).get("alg") != "RS256":
                return False
            with self.lock:
                key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.audience,
                issuer=self.issuer, options={"require": ["exp", "iat", "iss", "aud", "sub", "email"]},
            )
            return isinstance(claims["email"], str) and claims["email"].lower() in self.emails
        except (jwt.PyJWTError, ValueError, TypeError, OSError):
            return False
