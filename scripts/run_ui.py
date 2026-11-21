"""Launch the optional, loopback-only HITL & Evaluation workbench."""
import argparse
import json
import os
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from collab.ui_service import UIService, GROUPS


def build_ui(service):
    import gradio as gr

    download_cache = tempfile.TemporaryDirectory(prefix='collab-ui-downloads-')

    def download(paths):
        # Only explicitly exported files enter the serving cache; never expose runtime.
        batch = Path(download_cache.name) / uuid.uuid4().hex
        results = []
        root = Path(os.path.commonpath(paths)) if paths else None
        group_export = root is not None and root.name == 'export'
        archive = None
        if group_export:
            batch.mkdir(parents=True, exist_ok=True)
            archive = zipfile.ZipFile(batch / 'complete_execution_archive.zip', 'w', zipfile.ZIP_DEFLATED)
        try:
            for index, source in enumerate(paths):
                source = Path(source)
                data = source.read_bytes()
                content = data.decode('utf-8')
                if service.redact(content) != content:
                    raise ValueError('Export contains a configured secret; download blocked. Review the local export securely.')
                relative = source.relative_to(root) if group_export else Path(source.name)
                if archive:
                    archive.writestr(relative.as_posix(), data)
                # Historical artifact snapshots remain in the archive, not the delivery list.
                if group_export and relative.parts[0] not in {'delivery', 'metrics.json', 'performance_report.md', 'dialogue.md', 'dialogue.jsonl'}:
                    continue
                name = '__'.join(relative.parts)
                target = batch / str(index) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                results.append(str(target))
        finally:
            if archive:
                archive.close()
        if archive:
            results.append(str(batch / 'complete_execution_archive.zip'))
        return results

    def guarded(fn):
        def call(*args):
            try:
                result = fn(*args)
                return result if result is not None else 'Saved'
            except Exception as exc:
                raise gr.Error(service.redact(f'{type(exc).__name__}: {exc}')) from None
        return call

    from collab import ui_views as views

    def refresh(group, channel, selected, previous_pending):
        state = service.snapshot(group, channel)
        pending = [(f"{q['id']} · {q['kind']}", f"{group}:{channel}:{q['id']}")
                   for q in state['requests'] if q['status'] == 'pending']
        signature = json.dumps([group, channel, pending])
        selected_before = selected
        selected = selected if selected in [v for _, v in pending] else (pending[0][1] if pending else None)
        pending_update = selected
        request = next((q for q in state['requests'] if selected and selected.endswith(':' + q['id'])), None)
        control = {k: state[k] for k in ('execution', 'agent_status', 'tasks', 'queue', 'runs')}
        return (views.members(state), views.timeline(state), views.status(state),
                pending_update, gr.update(value=views.request_card(request), visible=bool(pending)),
                gr.update(visible=bool(request)), views.dashboard(state), control, state['requests'],
                state['metrics'], state['artifacts'], state['events'], signature,
                '' if selected != selected_before else gr.skip())

    def change_group(group):
        members = service.snapshot(group)['members']
        return gr.update(choices=[('Group chat', 'group'), *[(v, a) for a, v in members.items()]], value='group'), None, None

    def answer(group, channel, selected, text, decision, steps):
        prefix = f'{group}:{channel}:'
        if not selected or not selected.startswith(prefix):
            raise ValueError('Select a pending request in the current conversation')
        return service.answer_and_continue(group, selected[len(prefix):], text, decision, steps), ''

    def send(group, channel, text, mode, steps):
        return service.send(group, channel, text, mode, steps), ''

    with gr.Blocks(title='Campus Collab · Chat') as app:
        pending_signature = gr.State('')
        gr.Markdown('# Campus Collab\nMulti-Agent Workspace · Development trial / unofficial results', elem_id='brand')
        with gr.Row(elem_id='workspace'):
            with gr.Column(scale=1, min_width=200, elem_id='sidebar'):
                group = gr.Radio([(g.title(), g) for g in GROUPS], value='software', label='GROUPS', elem_id='groups')
                channel = gr.Dropdown([('Group chat', 'group')], value='group', label='Conversation / direct message')
                gr.Markdown('### AGENTS')
                members = gr.HTML()
                refresh_button = gr.Button('Refresh', size='sm')
            with gr.Column(scale=4, min_width=0, elem_id='chat-main'):
                heading = gr.HTML()
                transcript = gr.HTML(elem_id='conversation')
                request_id = gr.State(None)
                detail = gr.HTML()
                with gr.Group(visible=False) as human_controls:
                    response = gr.Textbox(label='Answer / rejection reason / approval note', lines=2)
                    with gr.Row():
                        clarify = gr.Button('Send answer')
                        approve = gr.Button('✓ Approve')
                        reject = gr.Button('✕ Reject')
                    gr.Markdown('Approving a tool may execute it immediately. Review deliverables before accepting.')
                with gr.Group():
                    text = gr.Textbox(show_label=False, placeholder='Message the team…', lines=2)
                    with gr.Row():
                        mode = gr.Radio([('New task', 'task'), ('Follow-up', 'message')], value='task', show_label=False)
                        send_button = gr.Button('Send ↑', variant='primary', min_width=100)
                    notice = gr.Textbox(show_label=False, interactive=False, placeholder='Send saves your message and starts execution using the configured model.')
                with gr.Row():
                    start = gr.Button('▶ Continue', variant='primary')
                    pause = gr.Button('Ⅱ Pause')
                    end = gr.Button('■ End')
                    steps = gr.Number(value=30, minimum=1, maximum=service.config.max_steps, precision=0, label='Actions per send')
        gr.Markdown('### PERFORMANCE DASHBOARD\nMeasured from persisted runs. Private tasks are excluded from group completion rates.')
        dashboard = gr.HTML()
        with gr.Accordion('Evaluation details', open=False):
            metrics_json = gr.JSON(label='Group metrics')
            benchmark_button = gr.Button('Evaluate all six benchmark tasks')
            benchmark_json = gr.JSON(label='Benchmark metrics')
        with gr.Accordion('Reports & deliverables', open=False):
            artifacts = gr.JSON(label='Published artifacts')
            export_button = gr.Button('Export reports, dialogue & artifacts')
            downloads = gr.File(label='Final deliveries, reports & full archive', file_count='multiple', interactive=False)
            export_benchmark_button = gr.Button('Export benchmark reports')
            benchmark_downloads = gr.File(label='Benchmark downloads', file_count='multiple', interactive=False)
            gr.Markdown('Files prefixed delivery__ are selected final deliverables. Older versions are in complete_execution_archive.zip. Full exports include private messages. Share only with authorized operators. Artifact hashes are verified; this UI does not run generated code.')
        with gr.Accordion('Execution log & advanced controls', open=False):
            control = gr.JSON(label='Tasks / queue / action budget')
            history = gr.JSON(label='Human request history')
            events = gr.JSON(label='Full events (current conversation)')
            abort_reason = gr.Textbox(label='Reason for aborting (recorded as failed)')
            abort = gr.Button('Abort group task')

        outputs = [members, transcript, heading, request_id, detail, human_controls, dashboard, control, history, metrics_json, artifacts, events, pending_signature, response]
        inputs = [group, channel, request_id, pending_signature]
        group.change(guarded(change_group), group, [channel, downloads, request_id], queue=False).then(guarded(refresh), inputs, outputs)
        app.load(guarded(change_group), group, [channel, downloads, request_id]).then(guarded(refresh), inputs, outputs)
        refresh_button.click(guarded(refresh), inputs, outputs, queue=False)
        channel.change(guarded(refresh), inputs, outputs, queue=False)
        gr.Timer(1).tick(guarded(refresh), inputs, outputs, queue=False, show_progress='hidden')
        for trigger in (send_button.click, text.submit):
            trigger(guarded(send), [group, channel, text, mode, steps], [notice, text]).then(guarded(refresh), inputs, outputs)
        start.click(guarded(service.start), [group, steps], notice)
        pause.click(guarded(service.pause), group, notice, queue=False)
        end.click(guarded(service.end), [group, channel], notice, queue=False)
        abort.click(guarded(service.abort), [group, abort_reason], notice)
        for button, decision in ((clarify, None), (approve, True), (reject, False)):
            button.click(guarded(lambda g, c, q, t, n, d=decision: answer(g, c, q, t, d, n)), [group, channel, request_id, response, steps], [notice, response]).then(guarded(refresh), inputs, outputs)
        benchmark_button.click(guarded(service.benchmark), outputs=benchmark_json)
        export_button.click(guarded(lambda g: download(service.export_group(g))), group, downloads)
        export_benchmark_button.click(guarded(lambda: download(service.benchmark(True))), outputs=benchmark_downloads)
    app.ui_js = views.CHAT_JS
    app.ui_css = views.CSS
    app.download_cache = download_cache
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=BASE / 'runtime')
    parser.add_argument('--port', type=int, default=7860)
    args = parser.parse_args(argv)
    os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
    # Do not inherit deployment settings that expose arbitrary local directories.
    os.environ.pop('GRADIO_ALLOWED_PATHS', None)
    os.environ.pop('GRADIO_SHARE', None)
    try:
        import gradio  # noqa: F401
    except ImportError:
        parser.exit(1, 'Install optional UI dependencies: python -m pip install -r requirements-ui.txt\n')
    service = UIService(args.runtime, BASE / 'NLP_Mid_Test')
    service.initialize()
    app = build_ui(service)
    app.queue().launch(server_name='127.0.0.1', server_port=args.port, share=False,
                       allowed_paths=[app.download_cache.name], blocked_paths=[str(BASE / '.env'), str(BASE / '.git')],
                       show_error=False, inbrowser=False, css=app.ui_css, js=app.ui_js)


if __name__ == '__main__':
    main()
