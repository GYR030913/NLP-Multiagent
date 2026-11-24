import unittest
from collab import ui_views
from tests import test_ui_service as fixtures


class ChatViewsTests(unittest.TestCase):
    setUp = fixtures.UIServiceTests.setUp
    tearDown = fixtures.UIServiceTests.tearDown

    def test_html_is_escaped(self):
        self.service.submit('software', '<script>alert(1)</script>')
        html = ui_views.timeline(self.service.snapshot('software'))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('Human', html)


    def test_empty_start_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'No task submitted'):
            self.service.start('software', 1)
        self.assertEqual(self.service.job('software'), {})


    def test_request_card_escapes_tool_parameters(self):
        card = ui_views.request_card({'id': 'q1', 'kind': 'approval', 'action': {'args': {'content': '<img onerror=evil>'}}})
        self.assertNotIn('<img', card)
        self.assertIn('Tool approval', card)
