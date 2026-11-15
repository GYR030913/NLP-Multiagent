"""Create groups without API calls; existing groups are preserved."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collab.scenarios import discover, load_scenario
from collab.store import Store
from collab.engine import Engine


def main():
    base = Path(__file__).resolve().parents[1]
    paths = discover(base / 'NLP_Mid_Test')
    if len(paths) != 3:
        raise ValueError('Expected all three benchmark scenario directories')
    for group, path in zip(('software', 'newsroom', 'fund'), paths):
        store = Store(base / 'runtime', group)
        if store.state_path.exists():
            print(group + ': already exists; preserved')
        else:
            Engine.create(store, load_scenario(path))
            print(group + ': created; no API calls made')


if __name__ == '__main__':
    main()
