import re
from pathlib import Path


def discover(root):
    return sorted(p for p in Path(root).glob('scenario_*') if p.is_dir())


def load_scenario(path):
    path = Path(path)
    text = (path / '01_role_initialization.md').read_text(encoding='utf-8-sig')
    roles = {}
    for match in re.finditer(r'^## ([^\n]+)\((\w+)\)\s*\n\s*```text\s*\n(.*?)```', text, re.M | re.S):
        title, agent, prompt = match.groups()
        roles[agent] = {'title': title.strip(), 'prompt': prompt.strip()}
    if len(roles) != 4:
        raise ValueError(f'Expected four role prompts in {path}')
    tasks = []
    for task_path in sorted(path.glob('0[23]_task_*.md')):
        raw = task_path.read_text(encoding='utf-8-sig')
        block = re.search(r'```text\s*\n(.*?)```', raw, re.S)
        if not block:
            raise ValueError(f'Missing task text in {task_path}')
        tasks.append({'file': task_path.name, 'text': block.group(1).strip()})
    return {'name': path.name, 'roles': roles, 'tasks': tasks}
