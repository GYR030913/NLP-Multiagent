"""Machine-readable tool contracts; validation happens before approval/execution."""
from fnmatch import fnmatchcase


def spec(required, optional=None, approval=False, network='none', side_effect='read'):
    return {'required': required, 'optional': optional or {}, 'approval': approval,
            'network': network, 'side_effect': side_effect, 'version': '1.0.0',
            'timeout_seconds': 120, 'output_limit_bytes': 32000,
            'retry_policy': 'idempotent-only' if side_effect != 'read' else 'bounded'}


REGISTRY = {
    'memory.search': spec({'query': str}, {'limit': int, 'include_candidates': bool}),
    'memory.inspect': spec({'memory_id': str}),
    'memory.propose': spec({'content': str, 'kind': str, 'evidence': list},
        {'scope': dict, 'valid_until': str, 'supersedes': str, 'operation_id': str}, side_effect='memory'),
    'memory.challenge': spec({'memory_id': str, 'reason': str, 'evidence': list}, side_effect='memory'),
    'memory.share': spec({'memory_id': str}, {'content': str}, approval=True, side_effect='memory'),
    'memory.working_set': spec({}, {'assumptions': list, 'open_questions': list}, side_effect='memory'),
    'web.fetch_document': spec({'url': str}, network='public-https', side_effect='document'),
    'document.extract': spec({'document_id': str}, {'max_pages': int}, side_effect='source'),
    'evidence.read_source': spec({'source_id': str}, {'offset': int, 'limit': int}),
    'evidence.read_event': spec({'seq': int}, {'offset': int, 'limit': int}),
    'evidence.find_quote': spec({'source_id': str, 'quote': str}),
    'evidence.record_claim': spec({'text': str, 'citations': list},
        {'as_of': str, 'limitations': str, 'supersedes': str}, side_effect='evidence'),
    'evidence.list_claims': spec({}, {'include_history': bool}),
    'data.register': spec({'path': str, 'name': str, 'provenance': dict},
        {'timezone': str, 'currency': str, 'as_of': str}, side_effect='dataset'),
    'data.analyze': spec({'dataset_id': str, 'spec': dict}),
    'data.export': spec({'dataset_id': str, 'path': str}, side_effect='workspace'),
    'files.list': spec({}), 'files.read': spec({'path': str}, {'offset': int, 'limit': int}),
    'files.write': spec({'path': str, 'content': str}, side_effect='workspace'),
    'files.append': spec({'path': str, 'content': str}, side_effect='workspace'),
    'files.replace': spec({'path': str, 'old': str, 'new': str}, {'expected_count': int}, side_effect='workspace'),
    'files.delete': spec({'path': str}, approval=True, side_effect='workspace'),
    'code.run': spec({'argv': list}, {'timeout': int, 'cwd': str}, side_effect='workspace'),
    'ui.check': spec({'path': str}),
    'web.search': spec({'query': str}, {'topic': str, 'time_range': str, 'include_domains': list}, network='public-https'),
    'web.fetch': spec({'url': str}, network='public-https'),
    'artifact.publish': spec({'path': str}, {'description': str}, side_effect='artifact'),
    'artifact.import': spec({'id': str, 'path': str}, side_effect='workspace'),
    'artifact.publish_bundle': spec({'paths': list}, {'description': str, 'entrypoints': list,
        'data_contract': dict, 'version': str, 'required_paths': list, 'idempotency_key': str}, side_effect='artifact'),
    'artifact.import_bundle': spec({'bundle_id': str, 'path': str}, side_effect='workspace'),
    'artifact.validate_bundle': spec({'bundle_id': str}),
    'skill.list': spec({}, {'capability': str}),
    'skill.use': spec({'id': str}, {'version': str}),
    'browser.verify': spec({'bundle_id': str, 'argv': list, 'checks': list},
                           {'port': int, 'timeout': int, 'expected_http_errors': list}, side_effect='verification'),
    'preview.start': spec({'bundle_id': str, 'argv': list}, {'port': int, 'app_port': int, 'lease_seconds': int, 'data_key': str},
                          approval=True, network='loopback-published', side_effect='preview'),
    'preview.stop': spec({'lease_id': str}, side_effect='preview'),
    'git.init': spec({}, side_effect='workspace'), 'git.status': spec({}), 'git.diff': spec({}),
    'git.commit': spec({'message': str}, side_effect='workspace'),
    'git.push': spec({'url': str}, {'branch': str}, approval=True, network='approved-github', side_effect='external'),
}

REGISTRY['code.run']['description'] = 'Offline Python stdlib/Git sandbox. Node is not installed here; use ui.check for JavaScript syntax and browser.verify for UI interactions.'
REGISTRY['code.run']['description'] += ' Optional cwd is an existing relative directory inside your workspace, e.g. the imported bundle directory. Set it when testing an imported version; default root may contain older copies. Returns cwd with exit_code/output.'
REGISTRY['code.run']['description'] += ' Failed Python runs may return failure_context with bounded source excerpts at exact workspace traceback lines. Use these locations for a small fix before repeating inventories.'
REGISTRY['browser.verify']['description'] = 'Offline browser check of an immutable bundle. Start the app with argv on port; use goto/fill/click/select/press/expect_text/expect_value/expect_visible/expect_count declarations. No arbitrary evaluate.'
REGISTRY['artifact.publish_bundle']['description'] = 'Publish all required text source/tests/docs together. Returns artifact_ids for finish. No ZIP, database, secrets or hidden paths.'
REGISTRY['evidence.record_claim']['description'] = 'Citations use exact source_id,start,end,quote,relation(supports/opposes/uncertain), optional PDF page. Read each passage in this task first. Snippets cannot verify claims; ledger validation does not certify truth.'
REGISTRY['evidence.list_claims']['description'] = 'List visible claims from the current run by default. include_history=true explicitly includes permitted earlier runs; old claims are not evidence read in this task.'
REGISTRY['web.search']['description'] = 'Discover sources; snippets are leads. Optional topic is exactly general, news, or finance. Optional time_range is exactly day, week, month, or year; omit it for historical research. Do not put date ranges or academic/research in these enum fields.'
REGISTRY['data.analyze']['description'] = 'Finite ops: booking_summary(start_date,end_date inclusive); allocation(capital_hkd,hkd_per_usd,fx_as_of,fee_rate,shocks); group_sum(group_by,column); detector_base_rate(). Exact Decimal calculations, no arbitrary SQL/eval.'
REGISTRY['data.register']['description'] = 'Register bounded CSV/JSON or an operator-approved inputs/bookings-HASH.sqlite3 snapshot. provenance requires input_kind:observed/operator_snapshot/simulation/user_provided, optional source_ids/description. Removes booker/title/email/phone.'
REGISTRY['data.register']['description'] += ' JSON accepts an array, {records:[...]}, or {bookings:[...],rooms:[{id,name}]}; use a short identifier for name.'
REGISTRY['data.analyze']['description'] += ' Booking rows: room_id,start_dt,end_dt,status(active/cancelled). Allocation rows: asset,weight (fraction 0..1); use asset CASH for HKD cash, USD for other assets. Detector scenario: one row with prevalence,sensitivity,specificity fractions.'
REGISTRY['memory.working_set']['description'] = 'Optional assumptions/open_questions are lists of {text:string,evidence:[visible event IDs]}; never plain strings. Assumptions remain unconfirmed.'
REGISTRY['files.read']['description'] = 'Read own file. For omitted/truncated sections use offset and limit<=1500, advancing the offset. Repeating the same oversized read does not recover omitted text.'
for name in ('files.write','files.append','files.replace'):
    REGISTRY[name]['description'] = 'Returns path, bytes, sha256 and changed. Identical content or restoring an already observed version does not reset the output checkpoint. Content changes are not proof of quality; test before delivery.'


def allowed(name, patterns):
    return any(fnmatchcase(name, pattern) for pattern in patterns)


def validate(name, args, patterns=('*',)):
    if name not in REGISTRY or not allowed(name, patterns):
        raise ValueError('Unknown or disallowed tool: ' + str(name))
    rule = REGISTRY[name]
    if not isinstance(args, dict):
        raise ValueError('Tool arguments must be an object')
    if set(args) - set(rule['required']) - set(rule['optional']):
        raise ValueError('Unknown tool parameters: ' + ', '.join(sorted(set(args) - set(rule['required']) - set(rule['optional']))))
    for field, kind in {**rule['required'], **rule['optional']}.items():
        if field not in args:
            if field in rule['required']:
                raise ValueError('Missing tool parameter: ' + field)
            continue
        if not isinstance(args[field], kind) or (kind is int and isinstance(args[field], bool)):
            raise ValueError('Invalid tool parameter type: ' + field)
    for field in ('argv', 'paths', 'entrypoints', 'required_paths', 'include_domains'):
        if field in args and not all(isinstance(value, str) and '\x00' not in value for value in args[field]):
            raise ValueError('Tool list must contain strings: ' + field)
    if name == 'preview.start':
        from .browser import validate_plan
        validate_plan({'argv': args['argv'], 'port': args.get('app_port', 8000),
                       'checks': [{'action': 'expect_visible', 'selector': 'body'}]})
    elif name == 'browser.verify':
        from .browser import validate_plan
        validate_plan(args)
    return rule


def descriptors(patterns=('*',)):
    result = []
    for name, rule in REGISTRY.items():
        if allowed(name, patterns):
            result.append({'name': name, **{k: v for k, v in rule.items() if k not in ('required', 'optional')},
                'required': {k: v.__name__ for k, v in rule['required'].items()},
                'optional': {k: v.__name__ for k, v in rule['optional'].items()}})
    return result
