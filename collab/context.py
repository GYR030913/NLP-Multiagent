"""Visibility filtering shared by context assembly and tool policy."""
from .tool_registry import REGISTRY, descriptors
from .skills import ROLE_CAPABILITIES


def tool_patterns(task):
    patterns = task.get('allowed_tools', ['*'])
    if task.get('output_checkpoint',{}).get('required'):
        from .tool_registry import allowed
        from .recovery import CHECKPOINT_TOOLS
        candidates=CHECKPOINT_TOOLS | ({'files.read'} if task['output_checkpoint'].get('repair_read') else set())
        patterns=[name for name in candidates if allowed(name,patterns)]
    if task.get('private'):
        from .tool_registry import allowed
        return [name for name in REGISTRY if allowed(name, patterns)
                and not name.startswith(('artifact.', 'preview.', 'browser.'))]
    return patterns


def visible_event(state, event, task):
    linked = event.get('task')
    related = state['tasks'].get(linked) if isinstance(linked, str) else None
    if related and related.get('private'):
        return task.get('private') and related['id'] == task['id']
    if event['kind'] in {'message', 'human_message', 'operator_test_message'}:
        return event.get('to') in {'group', task['owner']} or event.get('sender') == task['owner']
    return bool(related and related['owner'] == task['owner'])


def tool_context(task, roles):
    # Uniform registry audit metadata is not repeated for every tool in every
    # prompt. Permissions still use the complete registry, not this projection.
    schemas = [{k:v for k,v in item.items() if k in {'name','required','optional','description','approval','network'}}
               for item in descriptors(tool_patterns(task))]
    return {'available_tools': schemas,
            'tool_defaults': {'contract_version':'1.0.0','timeout_seconds':120,'output_limit_bytes':32000},
            'role_capabilities': {role: ROLE_CAPABILITIES.get(role, []) for role in roles}}


def event_view(event):
    # Envelope metadata is for adapters/audit. Do not repeat its data payload in
    # the model context alongside the legacy result field.
    return {key: value for key, value in event.items() if key not in {'envelope', 'verification_snapshot'}}


def normalized_result(event):
    return {**event.get('envelope', {}), 'data': event['result']}
