"""Versioned, read-only runtime workflow guidance, separate from host agent skills."""
import hashlib
import json
from pathlib import Path

ROLE_CAPABILITIES = {
    'lead': ['planning', 'requirements', 'release', 'software_build', 'software_verify'],
    'dev': ['software_build'], 'qa': ['software_verify'], 'release': ['release', 'software_verify'],
    'chief_editor': ['planning', 'requirements', 'writing', 'research', 'fact_check'], 'reporter': ['research'],
    'fact_checker': ['fact_check'], 'editor': ['writing'],
    'fund_manager': ['planning', 'requirements', 'finance'], 'research': ['research', 'finance'],
    'risk': ['risk', 'fact_check'], 'marketing': ['requirements', 'writing', 'release'],
}


class SkillRegistry:
    def __init__(self, root=None):
        self.root = Path(root) if root else Path(__file__).parent / 'runtime_skills'

    def get(self, skill_id, role, version=None):
        if not isinstance(skill_id, str) or not skill_id.replace('.', '').replace('_', '').isalnum():
            raise ValueError('Invalid skill ID')
        path = self.root / skill_id
        try:
            manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
            body = (path / 'SKILL.md').read_text(encoding='utf-8')
        except FileNotFoundError:
            raise ValueError('Unknown skill: ' + skill_id) from None
        if role not in manifest['compatible_roles']:
            raise ValueError('Skill is not compatible with this role')
        if version and version != manifest['version']:
            raise ValueError('Unknown skill version')
        if hashlib.sha256(body.encode()).hexdigest() != manifest['content_sha256']:
            raise ValueError('Skill hash mismatch; review and version changes before use')
        return {**manifest, 'instructions': body}

    def list(self, role, capability=None):
        result = []
        for path in sorted(self.root.glob('*/manifest.json')):
            metadata = json.loads(path.read_text(encoding='utf-8'))
            if role in metadata['compatible_roles'] and (not capability or capability in metadata['capabilities']):
                item = self.get(metadata['id'], role)
                result.append({k: v for k, v in item.items() if k != 'instructions'})
        return result

    def pinned(self, ref, role):
        skill = self.get(ref['id'], role, ref['version'])
        if ref.get('content_sha256') != skill['content_sha256']:
            raise ValueError('Pinned skill content changed; use a reviewed new version in a new task')
        return skill
