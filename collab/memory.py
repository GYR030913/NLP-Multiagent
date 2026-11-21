"""Scoped, versioned memory. The journal is authoritative; SQLite is disposable.

All callers that mutate memory must hold the group's writer lock. Outbox entries
are persisted before append, and operation IDs make crash replay idempotent.
"""
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import sqlite3
from .store import dump, now


STATES = {'candidate', 'legacy_unverified', 'peer_validated', 'active',
          'disputed', 'expired', 'retracted', 'superseded'}
KINDS = {'episodic', 'semantic', 'procedural', 'preference'}
STOP = set('the a an and or to for of in on is are be with this that task use from as it by'.split())


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def terms(text):
    words = set(re.findall(r'[a-z0-9_]{2,}', text.lower())) - STOP
    for segment in re.findall(r'[\u3400-\u9fff]+', text):
        words.update(segment[i:i+2] for i in range(len(segment)-1))
        if len(segment) == 1:
            words.add(segment)
    return words


def protect_text(text, limit=3000):
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise ValueError(f'Memory text must contain 1-{limit} characters')
    if re.search(r'-----BEGIN .*PRIVATE KEY|(?:api[_-]?key|password|access[_-]?token)\s*[:=]\s*\S+', text, re.I):
        raise ValueError('Credentials are prohibited in memory')
    for key in ('LLM_API_KEY', 'TAVILY_API_KEY', 'GITHUB_TOKEN'):
        secret = os.getenv(key, '')
        if secret and secret in text:
            raise ValueError('Credentials are prohibited in memory')
    return text.strip()


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Memory dates require an explicit timezone')
    return result


class Memory:
    def __init__(self, store, state):
        self.store, self.state = store, state
        self.root = store.path / 'memory-v2'
        self.root.mkdir(exist_ok=True)
        self.journal = self.root / 'events.jsonl'
        self.index = self.root / 'index.sqlite3'

    def operations(self):
        if not self.journal.exists():
            return []
        raw = self.journal.read_bytes()
        lines, result, seen = raw.splitlines(keepends=True), [], set()
        for i, line in enumerate(lines):
            try:
                op = json.loads(line)
                if not isinstance(op.get('operation_id'), str) or not isinstance(op.get('record'), dict):
                    raise ValueError('Invalid journal operation')
            except (ValueError, UnicodeError, AttributeError):
                # A torn append at EOF is recoverable, corruption in the middle is not.
                if i != len(lines)-1 or line.endswith(b'\n'):
                    raise ValueError('Memory journal corruption; operator repair required')
                quarantine = self.root / ('torn-tail-' + hashlib.sha256(line).hexdigest()[:16] + '.bin')
                quarantine.write_bytes(line)
                self.journal.write_bytes(b''.join(lines[:i]))
                self.state.setdefault('memory_warnings', []).append('Torn journal tail quarantined: ' + quarantine.name)
                break
            if op['operation_id'] not in seen:
                seen.add(op['operation_id'])
                result.append(op)
            if i == len(lines)-1 and not line.endswith(b'\n'):
                with self.journal.open('ab') as handle:
                    handle.write(b'\n')
        return result

    def records(self):
        return {op['record']['id']: op['record'] for op in self.operations()}

    def flush(self, fault=None):
        operations = self.operations()
        seen = {op['operation_id'] for op in operations}
        for item in self.state.setdefault('memory_outbox', []):
            if item.get('delivered'):
                continue
            if item['operation_id'] not in seen:
                with self.journal.open('ab') as handle:
                    handle.write((json.dumps({k:v for k,v in item.items() if k != 'delivered'}, ensure_ascii=False)+'\n').encode())
                    handle.flush()
                    os.fsync(handle.fileno())
                seen.add(item['operation_id'])
            if fault == 'after_append':
                raise RuntimeError('Injected crash after journal append')
            item['delivered'] = True
            self.store.save(self.state)
        self.rebuild()

    def write(self, record, operation_id, fault=None):
        outbox = self.state.setdefault('memory_outbox', [])
        old = next((op for op in outbox if op['operation_id'] == operation_id), None)
        if old:
            if fingerprint(old['record']) != fingerprint(record):
                raise ValueError('Memory operation ID reused with different content')
        else:
            outbox.append({'operation_id': operation_id, 'record': deepcopy(record), 'delivered': False})
            self.store.save(self.state)
        if fault == 'after_outbox':
            raise RuntimeError('Injected crash after outbox save')
        self.flush(fault)
        return record

    def rebuild(self):
        records = self.records()
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, owner TEXT, origin TEXT, conversation TEXT, status TEXT, body TEXT)')
            db.execute('DELETE FROM records')
            db.executemany('INSERT INTO records VALUES (?,?,?,?,?,?)', [
                (r['id'], r['owner'], r['origin_scope'], r.get('conversation_id'), r['status'], json.dumps(r,ensure_ascii=False))
                for r in records.values()])
        return {'records': len(records), 'strategy': 'scoped-deterministic-keywords-v1', 'index': 'rebuildable-sqlite'}

    def visible(self, record, task):
        if record.get('group') != self.store.path.name:
            return False
        if record['origin_scope'] == 'private':
            return bool(task.get('private') and record.get('conversation_id') == task['id'] and record['owner'] == task['owner'])
        if record['origin_scope'] != 'group_public':
            return False
        return record.get('visibility') == 'group_public' or record['owner'] == task['owner']

    def inspect(self, memory_id, task):
        record = self.records().get(memory_id)
        if not record or not self.visible(record, task):
            raise ValueError('Memory unavailable in this scope')
        return self.effective(record)

    def effective(self, record):
        result=deepcopy(record)
        scope=record.get('scope',{})
        expired=bool(record.get('valid_until') and timestamp(record['valid_until'])<=datetime.now(timezone.utc))
        key=scope.get('dataset_key')
        latest=self.state.get('dataset_heads',{}).get(key) if key else None
        incompatible=bool(latest and self.state['datasets'][latest]['sha256']!=scope.get('dataset_digest'))
        if (expired or incompatible) and record['status'] in {'candidate','peer_validated','active','legacy_unverified'}:
            result.update(stored_status=record['status'],status='expired',
                          expiry_reason='dataset_version_changed' if incompatible else 'valid_until_elapsed')
        return result

    def references(self, refs, task):
        from .context import visible_event
        if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
            raise ValueError('Memory needs 1-20 visible evidence event IDs')
        result = []
        for seq in refs:
            if not isinstance(seq, int) or isinstance(seq, bool) or not 1 <= seq <= len(self.state['events']):
                raise ValueError('Unknown memory evidence')
            event = self.state['events'][seq-1]
            if not visible_event(self.state, event, task):
                raise ValueError('Memory evidence is outside the task scope')
            result.append(seq)
        return sorted(set(result))

    def propose(self, task, content, kind, evidence, scope=None, valid_until=None, supersedes=None, operation_id=None, outcome=None):
        content = protect_text(content)
        if kind not in KINDS:
            raise ValueError('Unknown memory kind')
        scope = scope or {}
        if not isinstance(scope, dict) or set(scope)-{'conditions','capabilities','dynamic','dataset_key','dataset_digest','subject_digest'}:
            raise ValueError('Invalid memory applicability scope')
        protect_text(json.dumps(scope,ensure_ascii=False), 5000)
        for field in ('conditions','capabilities'):
            if field in scope and (not isinstance(scope[field],list) or len(scope[field])>20 or any(not isinstance(v,str) or len(v)>300 for v in scope[field])):
                raise ValueError('Memory conditions/capabilities must be bounded string lists')
        if kind == 'semantic' and task['owner'] in {'fund_manager','research','risk','marketing'} and 'dynamic' not in scope:
            scope={**scope,'dynamic':True}
        refs = self.references(evidence, task)
        if operation_id:
            pending=next((op for op in self.state.get('memory_outbox',[]) if op['operation_id']==operation_id),None)
            if pending:
                prior=pending['record']
                if prior['content']!=content or prior['owner']!=task['owner'] or prior['scope']!=scope or prior['kind']!=kind or not set(refs)<=set(prior['support_refs']):
                    raise ValueError('Memory operation ID reused with different proposal')
                self.flush()
                return prior
        if valid_until:
            timestamp(valid_until)
        elif scope.get('dynamic'):
            valid_until = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        identity = [task['owner'], task['id'] if task.get('private') else None, kind, content.casefold(), scope]
        memory_id = 'm_' + fingerprint(identity)[:24]
        old = self.records().get(memory_id)
        if old and old.get('purged'):
            raise ValueError('Memory was purged; cannot resurrect the same identity')
        if old:
            record = deepcopy(old)
            merged = sorted(set(old['support_refs'])|set(refs))
            if merged == old['support_refs']:
                return old
            record.update(version=old['version']+1, support_refs=merged, updated_at=now())
        else:
            record = {'id': memory_id, 'version': 1, 'group': self.store.path.name,
                      'owner': task['owner'], 'visibility': 'role_private',
                      'origin_scope': 'private' if task.get('private') else 'group_public',
                      'conversation_id': task['id'] if task.get('private') else None,
                      'task': task['id'], 'run': task['run'], 'kind': kind, 'content': content,
                      'scope': scope, 'status': 'candidate', 'support_refs': refs, 'counter_refs': [],
                      'created_at': now(), 'updated_at': now(), 'valid_until': valid_until,
                      'supersedes': supersedes, 'outcome': outcome}
        if supersedes:
            prior = self.inspect(supersedes, task)
            if prior['owner'] != task['owner']:
                raise ValueError('Cannot replace another role memory')
        return self.write(record, operation_id or 'propose:' + fingerprint([memory_id,refs]))

    def transition(self, memory_id, status, actor, evidence, reason, task=None):
        if status not in STATES or status in {'candidate','legacy_unverified'}:
            raise ValueError('Invalid memory transition')
        record = deepcopy(self.records()[memory_id])
        if task and not self.visible(record, task):
            raise ValueError('Memory unavailable in this scope')
        if actor not in {'human','operator'}:
            if status != 'peer_validated' or actor == record['owner']:
                raise ValueError('Memory promotion requires independent review or human confirmation')
            if not task or task['owner'] != actor:
                raise ValueError('Independent reviewer task required')
            self.references(evidence, task)
        if status in {'active','peer_validated'} and (record['counter_refs'] or record.get('purged')):
            raise ValueError('Resolve counterevidence with a corrected memory version first')
        if status == 'peer_validated' and not evidence:
            raise ValueError('Independent memory validation needs evidence')
        record.update(status=status,version=record['version']+1,updated_at=now(),
                      status_reason=protect_text(reason),validated_by=actor)
        return self.write(record,'transition:'+fingerprint([memory_id,status,actor,evidence,reason,record['version']]))

    def challenge(self, task, memory_id, reason, evidence):
        record = self.inspect(memory_id,task)
        refs = self.references(evidence,task)
        operation='challenge:'+fingerprint([memory_id,refs,reason])
        prior=next((op for op in self.state.get('memory_outbox',[]) if op['operation_id']==operation),None)
        if prior:
            self.flush()
            return prior['record']
        record.update(status='disputed',version=record['version']+1,updated_at=now(),
                      counter_refs=sorted(set(record['counter_refs'])|set(refs)),
                      challenge_reason=protect_text(reason))
        return self.write(record,operation)

    def share(self, task, memory_id, content=None, private_approval=False):
        record = self.inspect(memory_id,task)
        if record['owner'] != task['owner']:
            raise ValueError('Only the owner can share a memory')
        if record['origin_scope'] == 'private':
            if not private_approval or not content:
                raise ValueError('Private-derived sharing needs approval of explicit redacted text')
            text = protect_text(content)
            public = deepcopy(record)
            public.update(id='m_'+fingerprint(['shared',record['id'],text])[:24],content=text,
                          origin_scope='group_public',conversation_id=None,visibility='group_public',
                          task=None,run=None,support_refs=[],counter_refs=[],status='candidate',version=1,
                          shared_from_digest=fingerprint(record['id']),approved_private_release=True)
            public['scope']={}
            for field in ('legacy_path','legacy_line_hash','challenge_reason','status_reason','validated_by','supersedes'):
                public.pop(field,None)
        else:
            if record['visibility']=='group_public':
                return record
            public = deepcopy(record)
            public.update(visibility='group_public',version=record['version']+1)
        public.update(updated_at=now(),shared_by=task['owner'])
        return self.write(public,'share:'+fingerprint([public['id'],public['content'],public['version']]))

    def search(self, task, query, limit=6, include_candidates=False):
        if not isinstance(query,str) or len(query)>3000 or not 1<=limit<=20:
            raise ValueError('Invalid memory query/limit')
        query_terms=terms(query)
        matches=[]
        for record in self.records().values():
            if not self.visible(record,task):
                continue
            record=self.effective(record)
            allowed={'active','peer_validated'} | ({'candidate','legacy_unverified'} if include_candidates else set())
            if record['status'] not in allowed or record.get('purged'):
                continue
            if record.get('valid_until') and timestamp(record['valid_until']) <= datetime.now(timezone.utc):
                continue
            scope=record.get('scope',{})
            key=scope.get('dataset_key')
            if key:
                latest=self.state.get('dataset_heads',{}).get(key)
                if latest and self.state['datasets'][latest]['sha256'] != scope.get('dataset_digest'):
                    continue
            subject=scope.get('subject_digest')
            input_digests={x.get('sha256') for x in task.get('inputs',[]) if isinstance(x,dict)}
            if subject and input_digests and subject not in input_digests:
                continue
            overlap=query_terms & terms(record['content']+' '+json.dumps(scope,ensure_ascii=False))
            if not overlap:
                continue
            matches.append((len(overlap),record['status']=='active',record['updated_at'],
                            {**deepcopy(record),'match_terms':sorted(overlap),'retrieval_reason':'scope permitted; live status; keyword overlap'}))
        matches.sort(key=lambda x:x[:3],reverse=True)
        return [m[3] for m in matches[:limit]]

    def import_legacy(self):
        # Never infer trust from old peer/human outcome labels.
        existing={op['operation_id'] for op in self.operations()}
        for path in self.store.path.glob('agents/**/memory.jsonl'):
            relative=path.relative_to(self.store.path).as_posix()
            owner=path.relative_to(self.store.path).parts[1]
            for index,line in enumerate(path.read_text(encoding='utf-8').splitlines()):
                operation='legacy:'+fingerprint([relative,index,line])
                if operation in existing:
                    continue
                try:
                    row=json.loads(line)
                    content=protect_text(row['lesson'])
                except (ValueError,KeyError,TypeError):
                    continue
                task=self.state['tasks'].get(row.get('task'))
                origin='private' if task and task.get('private') else ('group_public' if task else 'legacy_isolated')
                record={'id':'m_'+fingerprint(operation)[:24],'version':1,'group':self.store.path.name,
                        'owner':owner,'visibility':'role_private','origin_scope':origin,
                        'conversation_id':task['id'] if origin=='private' else None,
                        'task':row.get('task'),'run':task.get('run') if task else None,
                        'kind':'episodic','content':content,'scope':{},'status':'legacy_unverified',
                        'support_refs':[],'counter_refs':[],'created_at':row.get('time',now()),'updated_at':now(),
                        'valid_until':None,'outcome':row.get('outcome'),'legacy_path':relative,'legacy_line_hash':fingerprint(line)}
                self.write(record,operation)

    def purge(self, memory_id, reason):
        """Operator-only physical memory erasure, distinct from retracting a lesson."""
        records=self.records()
        old=records[memory_id]
        tombstone={**old,'content':'[purged]','purged':True,'status':'retracted',
                   'scope':{},'support_refs':[],'counter_refs':[],'status_reason':protect_text(reason),
                   'version':old['version']+1,'updated_at':now()}
        for key in ('challenge_reason','legacy_line_hash'):
            tombstone.pop(key,None)
        rewritten=[]
        for op in self.operations():
            rewritten.append({**op,'record':tombstone if op['record']['id']==memory_id else op['record']})
        temp=self.journal.with_suffix('.tmp')
        temp.write_text(''.join(json.dumps(op,ensure_ascii=False)+'\n' for op in rewritten),encoding='utf-8')
        temp.replace(self.journal)
        for item in self.state.get('memory_outbox',[]):
            if item['record']['id']==memory_id:
                item['record']=tombstone
        if old.get('legacy_path'):
            path=self.store.path/old['legacy_path']
            lines=path.read_text(encoding='utf-8').splitlines()
            path.write_text(''.join(line+'\n' for line in lines if fingerprint(line)!=old.get('legacy_line_hash')),encoding='utf-8')
        self.store.save(self.state)
        if self.index.exists():
            self.index.unlink()
        for path in self.root.glob('torn-tail-*.bin'):
            raw=path.read_bytes()
            if memory_id.encode() in raw or old['content'].encode() in raw:
                path.unlink()
        self.rebuild()
        return {'id':memory_id,'purged':True,'note':'Referenced raw run evidence and external backups are separate retention domains.'}
