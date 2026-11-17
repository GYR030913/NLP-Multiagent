import json
import tempfile
import unittest

from collab.config import Config
from collab.contracts import HumanInteractionGateway, MemoryGateway, ToolGateway
from collab.engine import Engine
from collab.report import metrics
from collab.store import Store
from collab.tools import Tools


ROLES = {
    name: {'title': name, 'prompt': 'You are ' + name}
    for name in ['lead', 'dev', 'qa', 'release']
}


class FakeClient:
    usage = {'total_tokens': 10}

    def __init__(self, actions=()):
        self.actions = iter(actions)

    def generate(self, messages):
        return next(self.actions)


class MemberAOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name, 'member-a')
        self.config = Config('hkgai', 'https://example.org', 'test', '')
        Engine.create(
            self.store,
            {
                'roles': ROLES,
                'tasks': [{'text': 'first'}, {'text': 'second'}],
            },
        )
        self.tools = Tools(self.store, self.config)
        self.engine = Engine(
            self.store, self.config, FakeClient(), self.tools
        )
        self.root = self.engine.submit('Build a reviewed artifact')

    def tearDown(self):
        self.temp.cleanup()

    def act(self, task_id, **action):
        self.engine.apply(self.engine.state['tasks'][task_id], action)
        self.store.save(self.engine.state)

    def plan_root(self, task_id=None):
        self.act(
            task_id or self.root,
            type='plan',
            criteria=['The accepted deliverable is complete.'],
            text='Use independent roles and verify the result.',
        )

    def tool(self, task_id, name, **args):
        self.act(task_id, type='tool', name=name, args=args)
        return self.engine.state['events'][-1]

    def finish(self, task_id, reviewer='qa', artifacts=None):
        self.act(
            task_id,
            type='finish',
            summary='Work delivered',
            lesson='Verify the artifact independently before delivery.',
            artifacts=artifacts or [],
            evidence=[],
            reviewer=reviewer,
        )
        return self.engine.state['tasks'][task_id].get('review_task')

    def approve_review(self, review_id):
        event = self.tool(
            review_id,
            'files.write',
            path='review.txt',
            content='Independent check',
        )
        target = self.engine.state['tasks'][review_id]['target']
        criteria = self.engine.state['tasks'][target]['criteria']
        self.act(
            review_id,
            type='review',
            approved=True,
            feedback='Verified',
            checks=[
                {
                    'criterion': criterion,
                    'passed': True,
                    'evidence': [event['seq']],
                }
                for criterion in criteria
            ],
        )

    def test_fake_model_drives_plan_then_delegation(self):
        self.engine.client = FakeClient(
            [
                {
                    'type': 'plan',
                    'criteria': [
                        'The child creates a concrete artifact.',
                        'An independent reviewer verifies the artifact.',
                    ],
                    'text': 'Split implementation and independent review.',
                },
                {
                    'type': 'delegate',
                    'to': 'dev',
                    'description': 'Implement the requested artifact.',
                    'criteria': ['The artifact exists and is understandable.'],
                },
            ]
        )

        self.engine.run(steps=2)

        root = self.engine.state['tasks'][self.root]
        self.assertTrue(root['planned'])
        self.assertEqual(len(root['criteria']), 2)
        self.assertEqual(len(root['children']), 1)

        child = self.engine.state['tasks'][root['children'][0]]
        self.assertEqual(child['owner'], 'dev')
        self.assertEqual(child['parent'], self.root)
        self.assertEqual(child['depth'], 1)
        self.assertEqual(
            child['criteria'], ['The artifact exists and is understandable.']
        )
        action_types = [
            event['action']['type']
            for event in self.engine.state['events']
            if event['kind'] == 'agent_action'
        ]
        self.assertEqual(action_types, ['plan', 'delegate'])

    def test_teammate_gateways_are_structurally_compatible(self):
        self.assertIsInstance(self.tools, ToolGateway)
        self.assertIsInstance(self.store, MemoryGateway)
        self.assertIsInstance(self.engine, HumanInteractionGateway)

    def test_multiple_dependencies_wake_only_after_every_prerequisite(self):
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Implement the core.',
            criteria=['Core implementation is complete.'],
        )
        implementation = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(
            self.root,
            type='delegate',
            to='release',
            description='Prepare release notes.',
            criteria=['Release notes are complete.'],
        )
        notes = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(
            self.root,
            type='delegate',
            to='qa',
            description='Combine and verify both outputs.',
            criteria=['Both prerequisite outputs are present.'],
            depends_on=[implementation, notes],
        )
        verification = self.engine.state['tasks'][self.root]['children'][-1]

        self.assertEqual(
            self.engine.state['tasks'][verification]['status'],
            'waiting_for_dependencies',
        )
        self.assertNotIn(verification, self.engine.state['queue'])
        self.assertIn(implementation, self.engine.state['queue'])
        self.assertIn(notes, self.engine.state['queue'])

        self.approve_review(self.finish(implementation, reviewer='qa'))
        self.assertEqual(
            self.engine.state['tasks'][verification]['status'],
            'waiting_for_dependencies',
        )
        self.assertNotIn(verification, self.engine.state['queue'])

        self.approve_review(self.finish(notes, reviewer='dev'))
        self.assertEqual(
            self.engine.state['tasks'][verification]['status'], 'running'
        )
        self.assertIn(verification, self.engine.state['queue'])
        with self.assertRaisesRegex(ValueError, 'Children still pending'):
            self.finish(self.root, reviewer='release')

    def test_cross_agent_dependency_cycle_is_rejected_atomically(self):
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Implement.',
            criteria=['Implementation exists.'],
        )
        implementation = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(
            self.root,
            type='delegate',
            to='release',
            description='Package.',
            criteria=['Package exists.'],
        )
        package = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(
            implementation,
            type='delegate',
            to='qa',
            description='Check package before release.',
            criteria=['Check is recorded.'],
            depends_on=[package],
        )
        check = self.engine.state['tasks'][implementation]['children'][-1]
        task_count = len(self.engine.state['tasks'])

        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.act(
                package,
                type='delegate',
                to='dev',
                description='This would close the dependency cycle.',
                criteria=['Cycle must be rejected.'],
                depends_on=[check],
            )

        self.assertEqual(len(self.engine.state['tasks']), task_count)
        self.assertEqual(
            self.engine.state['tasks'][package]['children'], []
        )

        with self.assertRaisesRegex(ValueError, 'another agent'):
            self.act(
                self.root,
                type='delegate',
                to='lead',
                description='Delegate to self.',
                criteria=['Must be rejected.'],
            )
        with self.assertRaisesRegex(ValueError, 'Unknown agent'):
            self.act(
                self.root,
                type='delegate',
                to='missing',
                description='Delegate to unknown agent.',
                criteria=['Must be rejected.'],
            )
        self.assertEqual(len(self.engine.state['tasks']), task_count)

    def test_depth_limit_does_not_reset_when_agent_changes(self):
        self.config.max_depth = 2
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Level one.',
            criteria=['Level one is complete.'],
        )
        level_one = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(
            level_one,
            type='delegate',
            to='qa',
            description='Level two.',
            criteria=['Level two is complete.'],
        )
        level_two = self.engine.state['tasks'][level_one]['children'][-1]
        task_count = len(self.engine.state['tasks'])

        with self.assertRaisesRegex(ValueError, 'Maximum delegation depth'):
            self.act(
                level_two,
                type='delegate',
                to='release',
                description='Level three must be rejected.',
                criteria=['This task must not be created.'],
            )

        self.assertEqual(
            self.engine.state['tasks'][level_one]['depth'], 1
        )
        self.assertEqual(
            self.engine.state['tasks'][level_two]['depth'], 2
        )
        self.assertEqual(len(self.engine.state['tasks']), task_count)

    def test_rejection_after_three_reworks_fails_without_success_metrics(self):
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Draft the implementation.',
            criteria=['The implementation exists.'],
        )
        work = self.engine.state['tasks'][self.root]['children'][-1]

        for rejection_number in range(1, 5):
            review = self.finish(work, reviewer='qa')
            self.act(
                review,
                type='review',
                approved=False,
                feedback=f'Draft {rejection_number} is incomplete.',
                checks=[],
            )
            if rejection_number < 4:
                self.assertEqual(
                    self.engine.state['tasks'][work]['status'], 'running'
                )

        self.assertEqual(self.engine.state['tasks'][work]['attempt'], 5)
        self.assertEqual(self.engine.state['tasks'][work]['status'], 'failed')
        self.assertEqual(
            self.engine.state['tasks'][self.root]['status'], 'failed'
        )
        self.assertEqual(
            self.engine.state['runs']['r1']['status'], 'failed'
        )
        self.assertEqual(metrics(self.engine.state)['task_completion_rate'], 0.)
        self.assertFalse(
            any(
                task['run'] == 'r1'
                for task_id, task in self.engine.state['tasks'].items()
                if task['status'] == 'running'
            )
        )

    def test_restart_recovers_running_tasks_and_skips_waiting_states(self):
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Parent with a child.',
            criteria=['Parent completes.'],
        )
        parent = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(
            parent,
            type='delegate',
            to='qa',
            description='Running grandchild.',
            criteria=['Grandchild completes.'],
        )
        grandchild = self.engine.state['tasks'][parent]['children'][-1]
        self.act(parent, type='wait')

        self.act(
            self.root,
            type='delegate',
            to='release',
            description='Waiting for a prerequisite.',
            criteria=['Dependency completes.'],
            depends_on=[parent],
        )
        dependency_wait = self.engine.state['tasks'][self.root]['children'][-1]

        self.act(
            self.root,
            type='delegate',
            to='release',
            description='Work currently under review.',
            criteria=['Review completes.'],
        )
        reviewing = self.engine.state['tasks'][self.root]['children'][-1]
        review = self.finish(reviewing, reviewer='qa')

        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Work waiting for a human.',
            criteria=['Human answer arrives.'],
        )
        human_wait = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(human_wait, type='ask', question='Which option?')

        self.engine.state['queue'] = []
        self.store.save(self.engine.state)
        restarted = Engine(
            self.store, self.config, FakeClient(), self.tools
        )

        expected_running = {self.root, grandchild, review}
        self.assertEqual(set(restarted.state['queue']), expected_running)
        self.assertEqual(
            len(restarted.state['queue']), len(set(restarted.state['queue']))
        )
        for task_id in (
            parent,
            dependency_wait,
            reviewing,
            human_wait,
        ):
            self.assertNotIn(task_id, restarted.state['queue'])

        self.store.save(restarted.state)
        restarted_again = Engine(
            self.store, self.config, FakeClient(), self.tools
        )
        self.assertEqual(
            set(restarted_again.state['queue']), expected_running
        )
        self.assertEqual(
            len(restarted_again.state['queue']),
            len(set(restarted_again.state['queue'])),
        )

    def test_agent_direct_message_is_recipient_only(self):
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Message recipient task.',
            criteria=['Recipient sees the message.'],
        )
        recipient_task = self.engine.state['tasks'][self.root]['children'][-1]
        self.act(
            self.root,
            type='delegate',
            to='qa',
            description='Unrelated task.',
            criteria=['Unrelated task sees only group messages.'],
        )
        unrelated_task = self.engine.state['tasks'][self.root]['children'][-1]

        self.act(
            self.root,
            type='message',
            to='dev',
            text='PRIVATE-HANDOFF',
        )
        self.act(
            self.root,
            type='message',
            to='group',
            text='GROUP-STATUS',
        )

        recipient_context = json.dumps(
            self.engine.context(
                self.engine.state['tasks'][recipient_task]
            )
        )
        unrelated_context = json.dumps(
            self.engine.context(
                self.engine.state['tasks'][unrelated_task]
            )
        )
        self.assertIn('PRIVATE-HANDOFF', recipient_context)
        self.assertNotIn('PRIVATE-HANDOFF', unrelated_context)
        self.assertIn('GROUP-STATUS', recipient_context)
        self.assertIn('GROUP-STATUS', unrelated_context)
        private_event = next(
            event
            for event in self.engine.state['events']
            if event.get('text') == 'PRIVATE-HANDOFF'
        )
        self.assertEqual(private_event['to'], 'dev')
        self.assertEqual(private_event['task'], self.root)

        with self.assertRaisesRegex(ValueError, 'Unknown recipient'):
            self.act(
                self.root,
                type='message',
                to='missing',
                text='This must not be delivered.',
            )
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.act(
                self.root,
                type='message',
                to='dev',
                text='   ',
            )

        private_task = self.engine.submit(
            'PRIVATE-TASK', owner='dev', private=True
        )
        with self.assertRaisesRegex(ValueError, 'cannot be forwarded'):
            self.act(
                private_task,
                type='message',
                to='group',
                text='Do not leak this private task.',
            )
        self.plan_root(private_task)
        with self.assertRaisesRegex(ValueError, 'Private chats cannot'):
            self.act(
                private_task,
                type='tool',
                name='artifact.publish',
                args={'path': 'private.txt'},
            )
        self.assertNotIn(
            private_task,
            self.engine.context(
                self.engine.state['tasks'][self.root]
            )['tasks'],
        )

    def test_review_cannot_reuse_tool_evidence_from_an_earlier_review(self):
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Build the first artifact.',
            criteria=['The first artifact exists.'],
        )
        first_work = self.engine.state['tasks'][self.root]['children'][-1]
        first_review = self.finish(first_work, reviewer='qa')
        old_evidence = self.tool(
            first_review,
            'files.write',
            path='first-review.txt',
            content='Only the first review was checked.',
        )
        self.act(
            first_review,
            type='review',
            approved=True,
            feedback='The first artifact passed.',
            checks=[
                {
                    'criterion': 'The first artifact exists.',
                    'passed': True,
                    'evidence': [old_evidence['seq']],
                }
            ],
        )

        self.act(
            self.root,
            type='delegate',
            to='release',
            description='Build the second artifact.',
            criteria=['The second artifact exists.'],
        )
        second_work = self.engine.state['tasks'][self.root]['children'][-1]
        second_review = self.finish(second_work, reviewer='qa')
        with self.assertRaisesRegex(ValueError, 'current task'):
            self.act(
                second_review,
                type='review',
                approved=True,
                feedback='Attempt to reuse the first review.',
                checks=[
                    {
                        'criterion': 'The second artifact exists.',
                        'passed': True,
                        'evidence': [old_evidence['seq']],
                    }
                ],
            )
        self.assertEqual(
            self.engine.state['tasks'][second_work]['status'], 'reviewing'
        )

    def test_task_budget_is_checked_before_review_creation(self):
        self.config.max_tasks = 2
        self.plan_root()
        self.act(
            self.root,
            type='delegate',
            to='dev',
            description='Fill the task budget.',
            criteria=['The only child exists.'],
        )
        child = self.engine.state['tasks'][self.root]['children'][-1]
        with self.assertRaisesRegex(ValueError, 'Task budget exhausted'):
            self.finish(child, reviewer='qa')
        self.assertEqual(len(self.engine.state['tasks']), 2)
        self.assertEqual(self.engine.state['tasks'][child]['status'], 'running')
        self.assertNotIn('submission', self.engine.state['tasks'][child])

    def test_run_action_budget_does_not_reset_on_resume(self):
        self.plan_root()
        run = self.engine.state['runs']['r1']
        run['max_actions'] = 1
        self.engine.client = FakeClient(
            [
                {'type': 'message', 'to': 'group', 'text': 'first'},
                {'type': 'message', 'to': 'group', 'text': 'second'},
            ]
        )

        self.engine.run(steps=1)
        self.engine.run(steps=1)

        self.assertEqual(run['actions_used'], 1)
        self.assertEqual(run['model_calls'], 1)
        self.assertTrue(run['budget_exhausted'])
        self.assertIn(self.root, self.engine.state['queue'])
        self.assertTrue(
            any(
                event['kind'] == 'run_budget_exhausted'
                for event in self.engine.state['events']
            )
        )

    def test_plan_rejects_duplicate_acceptance_criteria(self):
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            self.act(
                self.root,
                type='plan',
                criteria=['Same condition.', 'Same condition.'],
                text='Invalid duplicate plan.',
            )

    def test_second_task_imports_artifact_from_first_task(self):
        self.plan_root()
        self.tool(
            self.root,
            'files.write',
            path='report.md',
            content='# Reusable result\n',
        )
        artifact = self.tool(
            self.root, 'artifact.publish', path='report.md'
        )['result']['id']
        review = self.finish(
            self.root, reviewer='qa', artifacts=[artifact]
        )
        self.tool(
            review,
            'artifact.import',
            id=artifact,
            path='report.md',
        )
        self.tool(review, 'files.read', path='report.md')
        self.approve_review(review)

        acceptance = next(
            request
            for request in self.engine.state['requests'].values()
            if request['kind'] == 'acceptance'
        )
        self.engine.answer(acceptance['id'], approved=True)
        self.assertEqual(
            self.engine.state['runs']['r1']['status'], 'completed'
        )
        self.assertEqual(metrics(self.engine.state)['task_completion_rate'], 1.)

        second = self.engine.submit('Extend the previous result')
        self.plan_root(second)
        context = self.engine.context(self.engine.state['tasks'][second])
        self.assertIn(artifact, context['artifacts'])
        self.assertIn(self.root, context['tasks'])
        self.assertTrue(
            any(
                event.get('text') == 'Build a reviewed artifact'
                for event in context['recent_events']
            )
        )
        self.assertTrue(context['memory'])

        imported = self.tool(
            second,
            'artifact.import',
            id=artifact,
            path='reused.md',
        )
        self.assertEqual(imported['result']['imported'], artifact)
        reused = self.tools.execute(
            self.engine.state,
            self.engine.state['tasks'][second]['owner'],
            'files.read',
            {'path': 'reused.md'},
        )
        self.assertEqual(reused, '# Reusable result\n')


if __name__ == '__main__':
    unittest.main()
