"""Summarize a finished workflow run's failures, compared with the previous run on the same branch."""
import argparse
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

API = 'https://api.github.com'
ARTIFACT = 'failure-summary'
MAX_ITEMS = 10
MAX_TEXT = 220
TIMESTAMP = re.compile(r'^\d{4}-\d\d-\d\dT[\d:.]+Z ')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
PYTEST = re.compile(r'^\s*(FAILED|ERROR) +([\w./-]+(?:::[^\s]+|\.[^\s]+))(?: - (.*))?$')
TYPE_ERROR = re.compile(r'^error\[([\w-]+)\]: (.*)$')
TYPE_LOCATION = re.compile(r'^\s*--> (\S+?):(\d+)')
RUFF = re.compile(r'^(\S+\.py):(\d+):\d+: ([A-Z]+\d+) (.*)$')
LINT_CHECK = re.compile(r'^(\S+?)\.{3,}\[✗\]$')
EXIT = re.compile(r'^##\[error\]Process completed with exit code (\d+)\.$')
EXIT_HINTS = {
  126: 'a script is not executable (check its git file mode)',
  127: 'command not found',
  137: 'killed (out of memory or timeout)',
  143: 'terminated (timeout or cancellation)',
}
NOISE = ('##[', 'progress: ', 'shell: ', 'env:', '  ')


class GitHub:
  def __init__(self, repo, token):
    self.repo, self.token = repo, token

  def request(self, path):
    url = path if path.startswith('https://') else f'{API}/repos/{self.repo}/{path}'
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json'})
    with urllib.request.build_opener(_StripAuthOnRedirect()).open(request, timeout=120) as response:
      return response.read()

  def json(self, path):
    return json.loads(self.request(path))

  def jobs(self, run_id, attempt):
    jobs, page = [], 1
    while True:
      batch = self.json(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}')['jobs']
      jobs += batch
      if len(batch) < 100:
        return jobs
      page += 1

  def job_log(self, job_id):
    return self.request(f'actions/jobs/{job_id}/logs').decode(errors='replace')


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    new = super().redirect_request(req, fp, code, msg, headers, newurl)
    if new is not None and urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
      new.remove_header('Authorization')
    return new


def clean_lines(log):
  return [ANSI.sub('', TIMESTAMP.sub('', line)).rstrip() for line in log.splitlines()]


def group_name(job_name):
  return re.sub(r' \(\d+\)$', '', job_name)


def test_id(raw):
  if '::' not in raw:
    return raw
  path, _, rest = raw.partition('::')
  return path.removesuffix('.py').replace('/', '.') + '.' + rest.replace('::', '.')


def short(text):
  text = ' '.join(text.split()).replace('<', '&lt;').replace('>', '&gt;')
  return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 3] + '...'


def parse_log(lines):
  """Failure items from one job log: pytest cases, type errors, failed lint checks, else the failing command."""
  tests = {}
  for line in lines:
    if match := PYTEST.match(line):
      key = test_id(match.group(2))
      if match.group(3) or key not in tests:
        tests[key] = short(match.group(3) or '')
  items = [{'kind': 'test', 'title': key, 'detail': detail} for key, detail in tests.items()]

  for index, line in enumerate(lines):
    if match := TYPE_ERROR.match(line):
      location = next((m for m in map(TYPE_LOCATION.match, lines[index + 1:index + 4]) if m), None)
      title = f'{location.group(1)}:{location.group(2)}' if location else match.group(1)
      detail = short(f'{match.group(1)}: {match.group(2)}')
      items.append({'kind': 'type', 'title': title, 'detail': detail, 'id': f"{location.group(1) if location else ''}|{detail}"})
    elif match := RUFF.match(line):
      detail = short(f'{match.group(3)} {match.group(4)}')
      items.append({'kind': 'lint', 'title': f'{match.group(1)}:{match.group(2)}', 'detail': detail, 'id': f'{match.group(1)}|{detail}'})
    elif match := LINT_CHECK.match(line):
      items.append({'kind': 'lint', 'title': match.group(1), 'detail': ''})

  if items:
    return list({(item['kind'], item['title'], item['detail']): item for item in items}.values())
  for index, line in enumerate(lines):
    if match := EXIT.match(line):
      code = int(match.group(1))
      context = [text for text in lines[max(0, index - 8):index] if text.strip() and not text.startswith(NOISE)][-3:]
      detail = ' | '.join(short(text) for text in context)
      hint = EXIT_HINTS.get(code, '')
      return [{'kind': 'exit', 'title': f'exit {code}' + (f': {hint}' if hint else ''), 'detail': detail}]
  return []


def collect(github, run_id, attempt, own_job):
  failures, jobs = [], [job for job in github.jobs(run_id, attempt) if job['name'] != own_job and job['status'] == 'completed']
  for job in jobs:
    if job['conclusion'] not in ('failure', 'timed_out', 'startup_failure'):
      continue
    step = next((s['name'] for s in job.get('steps', []) if s.get('conclusion') == 'failure'), '')
    items = parse_log(clean_lines(github.job_log(job['id']))) if job['conclusion'] == 'failure' else []
    if not items:
      items = [{'kind': 'job', 'title': job['conclusion'].replace('_', ' '), 'detail': ''}]
    for item in items:
      failures.append({**item, 'group': group_name(job['name']), 'job': job['name'], 'step': step, 'url': job.get('html_url', '')})
  return len(jobs), failures


def failure_key(failure):
  return f"{failure['group']}|{failure['kind']}|{failure.get('id', failure['title'])}"


def previous_summary(github, workflow_id, branch, run_id):
  query = urllib.parse.urlencode({'branch': branch, 'status': 'completed', 'per_page': 100})
  for run in github.json(f'actions/workflows/{workflow_id}/runs?{query}')['workflow_runs']:
    if run['id'] >= run_id or run['conclusion'] == 'cancelled' or (run.get('head_repository') or {}).get('full_name') != github.repo:
      continue
    artifacts = github.json(f"actions/runs/{run['id']}/artifacts?name={ARTIFACT}")['artifacts']
    if artifacts and not artifacts[0]['expired']:
      with zipfile.ZipFile(io.BytesIO(github.request(artifacts[0]['archive_download_url']))) as archive:
        return json.loads(archive.read('summary.json'))
  return None


def new_commits(github, base, head):
  commits = github.json(f'compare/{base}...{head}').get('commits', [])
  return [f"{c['sha'][:10]} {c['commit']['author']['name']}: {c['commit']['message'].splitlines()[0]}" for c in commits][-15:]


def render_group(failures):
  lines, by_group = [], {}
  for failure in failures:
    by_group.setdefault(failure['group'], []).append(failure)
  for group, items in by_group.items():
    jobs = sorted({item['job'] for item in items})
    step = items[0]['step']
    lines.append(f"**{group}** ({len(items)})" + (f" · step `{step}`" if step else '') + (f" · [log]({items[0]['url']})" if items[0]['url'] else ''))
    if len(jobs) > 1:
      lines.append(f"  jobs: {', '.join(jobs)}")
    for item in items[:MAX_ITEMS]:
      lines.append(f"- `{item['title']}`" + (f" — {item['detail']}" if item['detail'] else ''))
    if len(items) > MAX_ITEMS:
      lines.append(f'- ... and {len(items) - MAX_ITEMS} more')
    lines.append('')
  return lines


def render(summary, previous, commits):
  failures, title = summary['failures'], f"{summary['branch']} @ {summary['sha'][:10]}"
  if not failures:
    lines = [f"## ✅ All {summary['jobs']} jobs passed ({title})"]
    if previous and previous['failures']:
      lines.append(f"Fixed since run {previous['run_id']}: {len(previous['failures'])} failures.")
    return '\n'.join(lines) + '\n'
  groups = len({f['group'] for f in failures})
  lines = [f"## ❌ {len(failures)} failures in {groups} job groups ({title})", '']
  if previous is None:
    lines += ['No earlier summary on this branch to compare with.', '', *render_group(failures)]
    return '\n'.join(lines)
  before = {failure_key(f) for f in previous['failures']}
  now = {failure_key(f) for f in failures}
  new = [f for f in failures if failure_key(f) not in before]
  still = [f for f in failures if failure_key(f) in before]
  fixed = [f for f in previous['failures'] if failure_key(f) not in now]
  lines += [f"Compared with run {previous['run_id']} ({previous['sha'][:10]}): 🆕 {len(new)} new · 🔁 {len(still)} still failing · ✅ {len(fixed)} fixed", '']
  if new:
    lines += ['### 🆕 New failures', '', *render_group(new)]
    if commits:
      lines += ['Commits since that run:', '', *[f'- {c}' for c in commits], '']
  if still:
    lines += ['<details><summary>🔁 Still failing</summary>', '', *render_group(still), '</details>', '']
  if fixed:
    lines += ['<details><summary>✅ Fixed</summary>', '', *render_group(fixed), '</details>', '']
  return '\n'.join(lines)


def main(arguments=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--job', required=True, help='name of the summary job itself')
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args(arguments)
  env = os.environ
  github = GitHub(env['GITHUB_REPOSITORY'], env['GH_TOKEN'])
  run_id = int(env['GITHUB_RUN_ID'])
  run = github.json(f'actions/runs/{run_id}')
  job_count, failures = collect(github, run_id, env.get('GITHUB_RUN_ATTEMPT', '1'), args.job)
  summary = {'run_id': run_id, 'sha': run['head_sha'], 'branch': run['head_branch'], 'jobs': job_count, 'failures': failures}
  previous = previous_summary(github, run['workflow_id'], run['head_branch'], run_id)
  commits = []
  if previous and previous['sha'] != summary['sha'] and any(failure_key(f) not in {failure_key(p) for p in previous['failures']} for f in failures):
    commits = new_commits(github, previous['sha'], summary['sha'])
  args.output.mkdir(parents=True, exist_ok=True)
  (args.output / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
  markdown = render(summary, previous, commits)
  (args.output / 'summary.md').write_text(markdown)
  if env.get('GITHUB_STEP_SUMMARY'):
    with open(env['GITHUB_STEP_SUMMARY'], 'a') as stream:
      stream.write(markdown)
  print(markdown)


if __name__ == '__main__':
  main()
