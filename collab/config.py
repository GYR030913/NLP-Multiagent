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
                os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))


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
    no_progress_limit: int = 5
    browser_image: str = 'campus-collab-browser:local'
    document_image: str = 'campus-collab-document:local'
    input_budget_tokens: int = 24000
    memory_enabled: bool = True
    compaction_enabled: bool = True

    @classmethod
    def from_env(cls):
        load_env()
        provider = os.getenv('LLM_PROVIDER', 'deepseek').lower()
        if provider not in {'deepseek', 'hkgai'}:
            raise ValueError('Only DeepSeek and HKGAI are allowed.')
        base = os.getenv('LLM_BASE_URL', 'https://api.deepseek.com' if provider == 'deepseek' else '')
        parts = urlsplit(base)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise ValueError('Configure a valid HTTPS LLM_BASE_URL.')
        if provider == 'deepseek' and parts.hostname != 'api.deepseek.com':
            raise ValueError('DeepSeek must use api.deepseek.com; use hkgai for your course endpoint.')
        if any(x in parts.hostname.lower() for x in ('openai', 'anthropic')):
            raise ValueError('OpenAI/Anthropic endpoints are prohibited by the course.')
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
                   transport=transport,
                   no_progress_limit=int(os.getenv('LLM_NO_PROGRESS_LIMIT', '5')),
                   browser_image=os.getenv('BROWSER_SANDBOX_IMAGE', 'campus-collab-browser:local'),
                   document_image=os.getenv('DOCUMENT_SANDBOX_IMAGE', 'campus-collab-document:local'),
                   input_budget_tokens=int(os.getenv('CONTEXT_INPUT_BUDGET', '24000')),
                   memory_enabled=os.getenv('MEMORY_ENABLED', '1') != '0',
                   compaction_enabled=os.getenv('CONTEXT_COMPACTION_ENABLED', '1') != '0')
