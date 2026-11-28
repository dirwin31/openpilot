from tools.ci.failure_summary import failure_key, group_name, parse_log, previous_summary, render


def summary(*failures, run_id=2, sha='b' * 40):
  return {'run_id': run_id, 'sha': sha, 'branch': 'testing', 'jobs': 3, 'failures': list(failures)}


def failure(title, group='unit tests', kind='test'):
  return {'group': group, 'job': group, 'kind': kind, 'title': title, 'detail': '', 'step': 'Run', 'url': ''}


def test_matrix_suffix_is_dropped_from_group_names():
  assert group_name('custom pytest (12)') == 'custom pytest'
  assert group_name('developer commands (macos-latest)') == 'developer commands (macos-latest)'


def test_path_and_dotted_pytest_ids_are_one_failure_with_its_message():
  items = parse_log([
    'FAILED openpilot/a/tests/test_x.py::test_y[1] - assert False',
    ' FAILED openpilot.a.tests.test_x.test_y[1] ',
    'ERROR: not a test id',
  ])
  assert items == [{'kind': 'test', 'title': 'openpilot.a.tests.test_x.test_y[1]', 'detail': 'assert False'}]


def test_type_ruff_and_lint_check_failures_are_listed_once():
  items = parse_log([
    'ty' + '.' * 10 + '[✗]',
    'error[invalid-assignment]: bad value',
    '  --> openpilot/a.py:7:3',
    'error[invalid-assignment]: bad value',
    '  --> openpilot/a.py:7:3',
    'openpilot/b.py:3:1: F401 unused import',
  ])
  assert [(item['kind'], item['title']) for item in items] == [
    ('lint', 'ty'), ('type', 'openpilot/a.py:7'), ('lint', 'openpilot/b.py:3')]


def test_type_error_identity_ignores_line_moves_and_messages_are_escaped():
  def item(line):
    return parse_log(['error[invalid-argument-type]: <object at 0x1>', f'  --> openpilot/a.py:{line}:1'])[0]
  first, moved = item(7), item(30)
  assert first['title'] != moved['title']
  assert failure_key({**first, 'group': 'static analysis'}) == failure_key({**moved, 'group': 'static analysis'})
  assert '<' not in first['detail'] and '&lt;object' in first['detail']


def test_baseline_ignores_fork_runs_and_newer_runs():
  class Fake:
    repo = 'StarPilotHQ/StarPilot'

    def json(self, path):
      assert path.startswith('actions/workflows/')
      return {'workflow_runs': [
        {'id': 9, 'conclusion': 'failure', 'head_repository': {'full_name': self.repo}},
        {'id': 5, 'conclusion': 'failure', 'head_repository': {'full_name': 'someone/fork'}},
        {'id': 4, 'conclusion': 'cancelled', 'head_repository': {'full_name': self.repo}},
      ]}
  assert previous_summary(Fake(), 1, 'testing', 8) is None


def test_shell_failure_reports_exit_hint_and_preceding_output():
  items = parse_log([
    '##[group]Run replay',
    'line 1: test_processes.py: Permission denied',
    '##[error]Process completed with exit code 126.',
  ])
  assert items == [{'kind': 'exit', 'title': 'exit 126: a script is not executable (check its git file mode)',
                    'detail': 'line 1: test_processes.py: Permission denied'}]


def test_log_without_recognized_failure_returns_nothing():
  assert parse_log(['all good']) == []


def test_render_splits_new_still_and_fixed_and_lists_commits():
  previous = summary(failure('old.still'), failure('old.fixed'), run_id=1, sha='a' * 40)
  current = summary(failure('old.still'), failure('exit 126', group='process replay', kind='exit'))
  text = render(current, previous, ['abc ryan: change'])
  assert '1 new · 🔁 1 still failing · ✅ 1 fixed' in text
  new, rest = text.split('Still failing')
  assert 'process replay' in new and 'abc ryan: change' in new and 'old.still' not in new
  assert 'old.still' in rest and 'old.fixed' in rest


def test_render_without_baseline_or_failures():
  assert 'No earlier summary' in render(summary(failure('x')), None, [])
  assert render(summary(), summary(failure('x'), run_id=1), []).startswith('## ✅ All 3 jobs passed')
