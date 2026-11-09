"""Application integration credentials, distinct from upstream model credentials.

Only KeyHash belongs in private credential storage. Neither hash nor secret is
an ordinary Resource/public-list field. Scope/state checks belong to admission.
"""
import base64
from dataclasses import dataclass,field
import hashlib
import hmac
import re
import secrets
from pydantic import SecretStr

_TOKEN=re.compile(r'zpa_([0-9a-f]{16})_([A-Za-z0-9_-]{43})\Z')
_ID=re.compile(r'appkey_[0-9a-f]{32}\Z')
_SELECTOR=re.compile(r'[0-9a-f]{16}\Z')
_DUMMY=bytes(32)


@dataclass(frozen=True)
class KeyHash:
    key_id:str
    selector:str
    digest:bytes=field(repr=False)
    last4:str
    algorithm:str='sha256-v1'
    def __post_init__(self):
        if not _ID.fullmatch(self.key_id) or not _SELECTOR.fullmatch(self.selector) or not isinstance(self.digest,bytes) or len(self.digest)!=32 or self.algorithm!='sha256-v1' or not re.fullmatch(r'[A-Za-z0-9_-]{4}',self.last4):
            raise ValueError('Invalid private credential hash record')
    @property
    def prefix(self):return 'zpa_'+self.selector[:8]
    def public_reference(self):return {'key_id':self.key_id,'prefix':self.prefix,'last4':self.last4}


class IssuedKey:
    """Transient one-time secret. Repr is safe and no serializer is provided."""
    __slots__=('verifier','_secret')
    def __init__(self,verifier,secret):self.verifier=verifier;self._secret=SecretStr(secret)
    def __repr__(self):return f'IssuedKey(prefix={self.verifier.prefix!r}, secret=[REDACTED])'
    def take_secret(self):
        if self._secret is None:raise ValueError('Credential secret was already revealed')
        value=self._secret.get_secret_value();self._secret=None
        return value


def issue_key()->IssuedKey:
    key_id='appkey_'+secrets.token_hex(16)
    selector=secrets.token_hex(8)
    secret=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii').rstrip('=')
    token='zpa_'+selector+'_'+secret
    return IssuedKey(KeyHash(key_id,selector,hashlib.sha256(token.encode('ascii')).digest(),token[-4:]),token)


def _candidate(value):
    if isinstance(value,SecretStr):value=value.get_secret_value()
    if not isinstance(value,str) or len(value)>256:return b'invalid-key',None
    try:encoded=value.encode('ascii')
    except UnicodeEncodeError:return b'invalid-key',None
    match=_TOKEN.fullmatch(value)
    return encoded,match.group(1) if match else None


def lookup_selector(candidate)->str|None:
    """A bounded lookup hint, never proof of identity or scope."""
    return _candidate(candidate)[1]


def verify_key(candidate,expected:KeyHash|None)->bool:
    encoded,identifier=_candidate(candidate)
    digest=hashlib.sha256(encoded).digest()
    expected_digest=expected.digest if isinstance(expected,KeyHash) else _DUMMY
    matched=hmac.compare_digest(digest,expected_digest)
    return bool(matched and identifier is not None and isinstance(expected,KeyHash) and identifier==expected.selector)
