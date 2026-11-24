import shutil
import unittest
from collab.ui_check import embedded_html, check_source, require_valid


@unittest.skipUnless(shutil.which('node'), 'Node.js not installed for static JavaScript checks')
class JavaScriptRegressionTests(unittest.TestCase):
    def test_python_string_escape_can_break_every_page_button(self):
        javascript = r'''<button onclick="loadBookings()">Refresh</button><script>
function loadBookings() {
  return '<button onclick="cancelBooking(\''+id+'\')">Cancel</button>';
}
</script>'''
        ordinary = 'INDEX_HTML = """' + javascript + '"""'
        raw = 'INDEX_HTML = r"""' + javascript + '"""'
        self.assertEqual(check_source(ordinary, 'app.py')['exit_code'], 1)
        result = check_source(raw, 'app.py')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['checked_scripts'], 2)

    def test_invalid_javascript_cannot_be_published_as_valid(self):
        with self.assertRaisesRegex(ValueError, 'JavaScript syntax check failed'):
            require_valid('<script>const broken = ;</script>', 'page.html')

    def test_inline_click_handler_is_also_compiled(self):
        self.assertEqual(check_source('<button onclick="bad syntax !">Click</button>', 'page.html')['exit_code'], 1)

    def test_json_script_data_is_not_javascript(self):
        self.assertEqual(check_source('<script type="application/json">{"x":1}</script>', 'page.html')['checked_scripts'], 0)

    def test_semantic_header_and_independent_scripts_are_valid(self):
        source = '''<html><head></head><body><header>Booking</header>
<script>document.title = 'Booking';</script>
<script>document.body.dataset.ready = 'yes';</script></body></html>'''
        self.assertEqual(require_valid(source, 'page.html')['checked_scripts'], 2)

    def test_script_text_and_comments_do_not_count_as_shell_tags(self):
        source = '''<html><head></head><body><!-- <html><head><body> -->
<script>const example = '<head><body>';</script></body></html>'''
        self.assertEqual(require_valid(source, 'page.html')['exit_code'], 0)

    def test_duplicate_documents_are_blocked_even_without_scripts(self):
        with self.assertRaisesRegex(ValueError, 'Structural validation failed'):
            require_valid('<html><head></head><body></body></html>' * 2, 'page.html')


class ExtractionTests(unittest.TestCase):
    def test_python_is_parsed_without_importing_or_running_it(self):
        self.assertIsNone(embedded_html("raise RuntimeError('must not execute')\n"))
        self.assertEqual(embedded_html('INDEX_HTML = "<p>Hello</p>"'), '<p>Hello</p>')
