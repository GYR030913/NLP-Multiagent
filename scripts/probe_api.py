"""Minimal connectivity check. Never prints credentials or provider response bodies."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collab.config import Config
from collab.llm import Client, ModelError


def main():
    config = Config.from_env()
    print(json.dumps({'provider': config.provider, 'base_url': config.base_url,
                      'model': config.model, 'key_configured': bool(config.api_key)}))
    client = Client(config)
    try:
        result = client.generate([
            {'role': 'system', 'content': 'Return only a JSON object, without any explanation.'},
            {'role': 'user', 'content': 'Return a JSON object with a single field status whose value is ok.'},
        ])
    except ModelError as exc:
        print(str(exc))
        return 1
    print(json.dumps({'connection_ok': result.get('status') == 'ok',
                      'transport': client.transport_used, 'usage': client.usage}))
    return 0 if result.get('status') == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
