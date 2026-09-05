"""Check tracked source for accidental private artifacts and broken local Markdown links."""
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

root = Path(__file__).resolve().parents[1]
files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
errors = []
for name in filter(None, files):
    p = root / name
    parts = Path(name).parts
    if any(part in {'.venv', 'data', 'data-local', 'backups', 'node_modules', 'build', '.gradle'} for part in parts):
        errors.append((name, 'private/generated directory'))
    if p.name in {'.env', 'keystore.properties', 'auth.json', '.ssh-config'} or p.suffix in {'.keystore', '.jks', '.db', '.jsonl', '.apk', '.aab', '.pem', '.key'}:
        errors.append((name, 'private/generated file'))
    try:
        text = p.read_text(encoding='utf-8')
    except UnicodeError:
        continue
    for index, line in enumerate(text.splitlines(), 1):
        if re.search(r'\b(?:gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,}|sk-[A-Za-z0-9_-]{24,})', line):
            errors.append((f'{name}:{index}', 'possible provider credential'))
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', line) and not name.startswith('server/tests/'):
            errors.append((f'{name}:{index}', 'private key material'))
    if p.suffix == '.md':
        for target in re.findall(r'\]\(([^\n)]+)\)', text):
            target = target.split(' "')[0].strip('<>')
            if re.match(r'[a-zA-Z]+:', target) or target.startswith('#'):
                continue
            dest = (p.parent / unquote(target.split('#')[0])).resolve()
            if not dest.exists():
                errors.append((name, 'missing link target: ' + target))
for name, issue in errors:
    print(name, issue)
print(f'Checked {len(list(filter(None, files)))} tracked files; {len(errors)} issue(s).')
sys.exit(bool(errors))
