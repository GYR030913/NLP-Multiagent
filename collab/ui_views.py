"""Escaped, read-only presentation of persisted events for the local chat UI."""
from html import escape
import json
from datetime import datetime


def esc(value):
    return escape(str(value), quote=True)


def clock(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime('%H:%M:%S')
    except (ValueError, TypeError):
        return str(value or '')


def timeline(state):
    cards = []
    for event in state['events']:
        kind = event['kind']
        sender, body, style = 'System', '', 'system'
        if kind in {'message', 'human_message'}:
            sender = event.get('sender', 'human')
            body = event.get('text', '')
            style = 'human' if sender == 'human' else 'agent'
        elif kind == 'agent_action':
            sender = event.get('agent', 'Agent')
            action = event['action']
            mode = action.get('type')
            if mode == 'message':  # the persisted message event carries the actual text
                continue
            style = 'agent'
            labels = {'plan': 'Planning', 'delegate': 'Delegating', 'ask': 'Requesting clarification',
                      'tool': 'Calling tool', 'finish': 'Submitting work', 'review': 'Independent review', 'wait': 'Waiting for collaborators'}
            body = labels.get(mode, mode or 'Action')
            detail = action.get('text') or action.get('summary') or action.get('description') or action.get('feedback') or action.get('question') or ''
            body += '\n' + str(detail)
            if action.get('to'):
                body += '\n→ ' + action['to']
            if mode == 'tool':
                body += '\n' + str(action.get('name', ''))
        elif kind == 'human_request':
            request = event['request']
            body = f"Human input needed · {request['id']} · {request['kind']}\n" + str(request.get('question') or request.get('summary') or 'See the request card below')
            style = 'request'
        elif kind == 'human_answer':
            sender, style = 'Human', 'human'
            decision = event.get('approved')
            body = ('Approved' if decision is True else 'Rejected' if decision is False else 'Answer') + f" · {event.get('request', '')}\n{event.get('text', '')}"
        elif kind in {'model_error', 'action_error', 'fatal_error'}:
            body, style = f"{kind}\n{event.get('error', '')}", 'error'
        elif kind == 'tool_result':
            body = 'Tool result · ' + str(event.get('name', '')) + ' (details in execution log)'
        elif kind == 'execution_paused':
            body = 'Paused — late model response discarded; task can be resumed'
        elif kind in {'run_finished', 'run_budget_exhausted', 'human_abort', 'review_result'}:
            body = json.dumps({k: v for k, v in event.items() if k not in {'seq', 'time', 'kind'}}, ensure_ascii=False)
            body = kind + '\n' + body
        else:
            continue
        title = 'Human' if sender.lower() == 'human' else state['members'].get(sender, sender)
        cards.append(f'<article class="chat-event {style}"><div class="avatar">{esc(sender[:2].upper())}</div><div class="bubble"><div class="byline">{esc(title)} <span>{esc(clock(event.get("time")))} · #{event["seq"]}</span></div><div class="message-text">{esc(body)}</div></div></article>')
    revision = f"{state['group']}:{state['channel']}:{state['events'][-1]['seq'] if state['events'] else 0}"
    return f'<div class="chat-timeline" data-revision="{esc(revision)}">' + (''.join(cards) or '<div class="empty-chat">Start a conversation<br><small>Send a task below to start collaborating with your agents.</small></div>') + '</div>'


def status(state):
    job = state['execution']
    pending = sum(q['status'] == 'pending' for q in state['requests'])
    if job.get('active'):
        label = job.get('message', 'Running')
        tone = 'working'
    elif pending:
        label, tone = f'Waiting for human · {pending} request(s)', 'waiting'
    elif not state['tasks']:
        label, tone = 'Ready for your first message', 'idle'
    else:
        label, tone = job.get('message') or 'History loaded — ready to continue', 'idle'
    return f'<div class="chat-heading"><strong>{esc(state["group"].title())} · {esc("Group chat" if state["channel"] == "group" else state["channel"] + " · direct message")}</strong><span class="status {tone}">{esc(label)}</span></div>'


def members(state):
    return '<div class="member-list">' + ''.join(f'<div class="member"><b>{esc(title)}</b><small>{esc(agent)} · {esc(" / ".join(state["agent_status"][agent]))}</small></div>' for agent, title in state['members'].items()) + '</div>'


def request_card(request):
    if not request:
        return '<div class="request-card muted">No request selected. Human requests appear here when an agent needs your input.</div>'
    labels = {'clarification': 'Your answer is needed', 'approval': 'Tool approval', 'acceptance': 'Final acceptance'}
    content = request.get('question') or request.get('summary') or ''
    if request.get('action'):
        content += '\n' + json.dumps(request['action'], ensure_ascii=False, indent=2)
    if request.get('artifacts'):
        content += '\nArtifacts: ' + ', '.join(request['artifacts'])
    return f'<div class="request-card"><b>{esc(labels.get(request["kind"], request["kind"]))} · {esc(request["id"])}</b><div class="message-text">{esc(content)}</div></div>'


def dashboard(state):
    report = state['metrics']
    rate = report['task_completion_rate']
    values = [('Task success', 'N/A' if rate is None else f'{rate:.0%}'),
              ('Subtasks', f'{report["completed_subtasks"]}/{report["total_subtasks"]}'),
              ('Model calls', sum(r['model_calls'] for r in report['runs'])),
              ('Reported tokens', sum(r['reported_tokens'] for r in report['runs'])),
              ('Human wait', f'{report["total_human_wait_seconds"]:.1f}s'),
              ('Remaining actions', sum(r['remaining_actions'] for r in state['runs'] if r['status'] == 'running'))]
    return '<div class="metrics-strip">' + ''.join(f'<div><strong>{esc(value)}</strong><small>{esc(label)}</small></div>' for label, value in values) + '</div>'


CSS = """
.gradio-container {width:calc(100% - 32px) !important;max-width:1440px !important; margin:auto; font-family:system-ui,sans-serif;}
#brand {border-bottom:1px solid #e6e8eb;padding:14px 0 20px;margin-bottom:18px;}
#workspace {width:100%;align-items:stretch;}
#chat-main {min-width:0 !important;flex:1 1 0 !important;}
#sidebar {flex:0 0 210px !important;min-width:0 !important;background:#f8fafc;border-radius:14px;padding:18px;}
#groups .wrap {display:flex;flex-direction:column;}
#groups label {width:100%;box-sizing:border-box;}
.member {padding:12px 0;border-bottom:1px solid #e6e8eb;}
.member b {display:block;font-size:14px;color:#25334a;}
.member small {display:block;color:#64748b;margin-top:5px;font-size:12px;}
.chat-heading {display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:8px 0;}
.chat-heading strong {font-size:20px;}
.status {border-radius:20px;background:#f1f5f9;padding:7px 12px;font-size:12px;}
.status.working {background:#e8f1ff;color:#175cd3;}.status.waiting {background:#fff0df;color:#9c4b0b;}
.chat-timeline {height:clamp(480px, 68dvh, 1000px);resize:vertical;min-height:360px;box-sizing:border-box;overflow-y:auto;padding:14px;background:#fff;border:1px solid #e8ebef;border-radius:14px;}
.chat-event {display:flex;gap:12px;margin:0 0 20px;align-items:flex-start;}
.avatar {background:#2563eb;color:white;border-radius:12px;min-width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;}
.bubble {flex:1;min-width:0;}.byline {font-weight:650;color:#245ec0;font-size:14px;margin:0 0 6px;}
.byline span {font-weight:400;color:#94a3b8;font-size:11px;margin-left:8px;}
.message-text {white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;font-size:14px;color:#273244;}
.human {background:#f3f5f7;padding:14px;border-radius:12px;}.human .avatar {background:#64748b;}.human .byline {color:#475569;}
.system .avatar {background:#94a3b8;}.system .message-text {font-size:12px;color:#64748b;}
.request .avatar {background:#d97706;}.error .avatar {background:#dc2626;}.error .message-text {color:#b42318;}
.empty-chat {text-align:center;min-height:100%;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center;padding:24px 12px;color:#64748b;font-size:22px;}.empty-chat small {display:block;font-size:13px;margin-top:12px;}
.request-card {border:1px solid #f1d1ac;border-radius:12px;background:#fffcf7;padding:15px;}.request-card b {color:#a95012;}.muted {color:#8793a4;border-color:#e6e8eb;background:#fafbfc;font-size:13px;}
.metrics-strip {display:grid;grid-template-columns:repeat(6,1fr);gap:16px;padding:18px 0;}.metrics-strip strong {display:block;font-size:27px;color:#1e293b;}.metrics-strip small {display:block;color:#64748b;margin-top:7px;}
@media(max-width:760px) {.gradio-container {width:calc(100% - 16px) !important;} #workspace {flex-direction:column;} #sidebar {flex:auto !important;width:100%;box-sizing:border-box;} #chat-main {width:100%;} #groups .wrap {flex-direction:row;flex-wrap:wrap;} .member-list {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));} .metrics-strip {grid-template-columns:repeat(3,1fr);} .chat-timeline {height:clamp(360px,60dvh,720px);min-height:300px;}}
"""


CHAT_JS = r"""() => {
  if (window.__collabScrollObserver) window.__collabScrollObserver.disconnect();
  let lastRevision = null, lastNode = null, savedTop = 0, scheduled = false;
  const update = () => {
    scheduled = false;
    const root = document.querySelector('#conversation');
    const node = root?.querySelector('.chat-timeline');
    if (!node) return;
    const revision = node.dataset.revision;
    if (node !== lastNode) {
      node.addEventListener('scroll', () => { savedTop = node.scrollTop; }, {passive:true});
    }
    if (revision !== lastRevision) node.scrollTop = node.scrollHeight;
    else if (node !== lastNode) node.scrollTop = savedTop;
    savedTop = node.scrollTop;
    lastNode = node;
    lastRevision = revision;
  };
  const observer = new MutationObserver(() => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
  });
  observer.observe(document.body, {childList:true, subtree:true, characterData:true});
  window.__collabScrollObserver = observer;
  update();
}"""
