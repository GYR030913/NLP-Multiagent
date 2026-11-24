from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


def load_env(path=Path('.env')):
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                if key == 'LLM_ALLOW_INSECURE_HTTP':
                    # Security-relevant opt-in: only the real environment counts.
                    continue
                os.environ.setdefault(key, value.strip().strip('\"').strip("'"))


def _insecure_http_allowed():
    """Read the insecure-HTTP opt-in directly from the process environment.

    This flag relaxes a transport security rule, so it must be an explicit
    operator decision. A `.env` file must not be able to turn it on silently.
    """
    return os.getenv('LLM_ALLOW_INSECURE_HTTP', '').strip().lower() in {'1', 'true', 'yes'}


@dataclass
class Config:
    provider: str
    base_url: str
    model: str
    api_key: str
    search_key: str = ''
    image: str = 'campus-collab-sandbox:local'
    max_steps: int = 120
    max_run_actions: int = 360
    max_depth: int = 5
    max_tasks: int = 40
    auth_header: str = 'Authorization'
    max_output_tokens: int = 8192
    request_timeout_seconds: float = 120
    transport: str = 'auto'
    allow_insecure_http: bool = False

    @classmethod
    def from_env(cls):
        load_env()
        provider = os.getenv('LLM_PROVIDER', 'deepseek').lower()
        if provider not in {'deepseek', 'hkgai', 'newapi', 'custom'}:
            raise ValueError('Only DeepSeek, HKGAI and course-approved custom gateways are allowed.')
        base = os.getenv('LLM_BASE_URL', 'https://api.deepseek.com' if provider == 'deepseek' else '')
        parts = urlsplit(base)
        insecure = _insecure_http_allowed()
        if parts.scheme not in ({'https', 'http'} if insecure else {'https'}):
            raise ValueError('Configure a valid HTTPS LLM_BASE_URL, or set LLM_ALLOW_INSECURE_HTTP=1 for a trusted LAN gateway.')
        if not parts.hostname or parts.username or parts.password:
            raise ValueError('Configure a valid LLM_BASE_URL without embedded credentials.')
        if provider == 'deepseek' and parts.hostname != 'api.deepseek.com':
            raise ValueError('DeepSeek must use api.deepseek.com; use hkgai for your course endpoint.')
        if any(x in parts.hostname.lower() for x in ('openai', 'anthropic')):
            raise ValueError('OpenAI/Anthropic endpoints are prohibited by the course.')
        if provider in {'newapi', 'custom'} and not insecure and parts.scheme != 'https':
            raise ValueError('Custom gateways must use HTTPS unless LLM_ALLOW_INSECURE_HTTP=1 is set explicitly.')
        transport = os.getenv('LLM_TRANSPORT', 'auto').lower()
        if transport not in {'auto', 'curl', 'urllib'}:
            raise ValueError('LLM_TRANSPORT must be auto, curl, or urllib.')
        return cls(provider, base.rstrip('/'), os.getenv('LLM_MODEL', 'deepseek-chat'),
                   os.getenv('LLM_API_KEY', ''), os.getenv('TAVILY_API_KEY', ''),
                   os.getenv('SANDBOX_IMAGE', 'campus-collab-sandbox:local'),
                   auth_header=os.getenv('LLM_AUTH_HEADER', 'Authorization'),
                   max_output_tokens=int(os.getenv('LLM_MAX_OUTPUT_TOKENS', '8192')),
                   request_timeout_seconds=float(os.getenv('LLM_REQUEST_TIMEOUT_SECONDS', '120')),
                   max_run_actions=int(os.getenv('LLM_MAX_RUN_ACTIONS', '360')),
                   transport=transport, allow_insecure_http=insecure)
