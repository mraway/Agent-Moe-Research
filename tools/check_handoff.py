#!/usr/bin/env python3
"""Audit staged or committed Git blobs before publishing this handoff.

Standard library only; reads the actual Git blobs, not unstaged file contents.
This is a conservative allowlist and common-secret scan, not a guarantee that
arbitrary research prose contains no sensitive information.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MAX_TOTAL = 50 * 1024**2
MAX_FILE = 1024**2
ROOT_FILES = {'README.md', '.gitignore', 'HANDOFF_MANIFEST.json', 'HANDOFF_VALIDATION.md', 'CLAUDE_ARCHIVE.md'}
RESEARCH_LINES = {'codex', 'claude', 'claude_wgm', 'claude_cm', 'claude_pdm'}
RESEARCH_ROOT_FILES = {'README.md', 'requirements.txt', 'requirements-lock.txt', 'requirements-report.txt'}
DOC_SUFFIXES = {'.md', '.rst', '.css', '.html', '.svg'}
SCORER_CONFIG_DIRS = {'cm_configs', 'pdm_configs', 'wgm_configs'}
SECRETS = {
    'private-key': re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'github-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'openai-key': re.compile(rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b'),
    'huggingface-token': re.compile(rb'\bhf_[A-Za-z0-9]{30,}\b'),
    'aws-access-key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
}


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def allowed(name: str, body: bytes) -> bool:
    if name in ROOT_FILES or name == 'tools/check_handoff.py':
        return True
    p = PurePosixPath(name)
    if len(p.parts) < 3 or p.parts[0] != 'research_lines' or p.parts[1] not in RESEARCH_LINES:
        return False
    rel = PurePosixPath(*p.parts[2:])
    if str(rel) in RESEARCH_ROOT_FILES:
        return True
    if str(rel) == 'docs/archive/2026-09-27/requirements-closeout.txt':
        return True
    if str(rel) == 'scripts/research_v2/pilot_diagnostics/README.md':
        return True
    if (len(rel.parts) == 4 and rel.parts[:2] == ('scripts', 'research_v2')
            and rel.parts[2] in SCORER_CONFIG_DIRS and rel.suffix == '.json'):
        obj = json.loads(body)
        return isinstance(obj, dict) and bool(obj) and all(
            isinstance(value, (str, int, float, bool, type(None))) for value in obj.values())
    if any(part.startswith('.') or part == '__pycache__' for part in rel.parts):
        return False
    if rel.parts[0] == 'configs':
        if len(rel.parts) != 2 or rel.suffix != '.json':
            return False
        obj = json.loads(body)
        return isinstance(obj, dict) and 'scenarios' not in obj and bool({'agent_id', 'model_id'} & obj.keys())
    if str(rel).startswith('docs/research_v2/pilot_results/'):
        return False
    if rel.parts[0] == 'docs':
        return rel.suffix in DOC_SUFFIXES
    return rel.parts[0] in {'src', 'scripts', 'tests'} and rel.suffix in {'.py', '.sh'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--index', action='store_true', help='audit exactly what git add has staged')
    group.add_argument('--revision', default='HEAD', help='commit/tree to audit; default HEAD')
    args = parser.parse_args()
    records = git('ls-files', '--stage', '-z') if args.index else git('ls-tree', '-r', '-z', args.revision)
    entries = []
    errors = []
    for record in records.split(b'\0'):
        if not record:
            continue
        meta, raw_name = record.split(b'\t', 1)
        mode, middle, last = meta.decode().split()
        name = raw_name.decode('utf-8')
        if mode not in {'100644', '100755'}:
            errors.append(f'Non-regular file: {name}')
            continue
        if args.index and last != '0':
            errors.append(f'Unmerged index entry: {name}')
            continue
        entries.append((name, middle if args.index else last))
    if not entries:
        errors.append('Empty snapshot')
    request = b''.join(sha.encode() + b'\n' for _, sha in entries)
    output = subprocess.check_output(['git', '-C', str(ROOT), 'cat-file', '--batch'], input=request)
    cursor = 0
    files = {}
    python_files = 0
    for name, sha in entries:
        header_end = output.index(b'\n', cursor)
        actual_sha, kind, raw_size = output[cursor:header_end].split()
        size = int(raw_size)
        start = header_end + 1
        body = output[start:start + size]
        cursor = start + size + 1
        assert actual_sha.decode() == sha and kind == b'blob'
        files[name] = body
        if size > MAX_FILE:
            errors.append(f'File exceeds 1 MiB: {name} ({size} bytes)')
        try:
            decoded = body.decode('utf-8')
            if not allowed(name, body):
                errors.append(f'Outside source/docs allowlist: {name}')
            if name.endswith('.py'):
                ast.parse(decoded, filename=name)
                python_files += 1
            if name.endswith('.json'):
                json.loads(decoded)
        except (UnicodeError, ValueError, SyntaxError) as exc:
            errors.append(f'Invalid text/syntax: {name} ({type(exc).__name__})')
        if b'\x00' in body:
            errors.append(f'Binary/NUL content: {name}')
        for label, pattern in SECRETS.items():
            if pattern.search(body):
                errors.append(f'Potential {label}: {name}')
    total = sum(len(body) for body in files.values())
    if total > MAX_TOTAL:
        errors.append(f'Total exceeds 50 MiB: {total} bytes')
    manifest_body = files.get('HANDOFF_MANIFEST.json')
    if manifest_body:
        manifest = json.loads(manifest_body)
        recorded = set()
        for entry in manifest['files']:
            if entry['path'] in recorded:
                errors.append(f'Duplicate manifest path: {entry["path"]}')
            recorded.add(entry['path'])
            body = files.get(entry['path'])
            if body is None or len(body) != entry['bytes'] or hashlib.sha256(body).hexdigest() != entry['sha256']:
                errors.append(f'Imported snapshot differs from manifest: {entry["path"]}')
        actual_research = {name for name in files if name.startswith('research_lines/')}
        if recorded != actual_research:
            errors.append('Research file set does not exactly match manifest')
    else:
        errors.append('Missing handoff manifest')
    largest = sorted(files, key=lambda name: len(files[name]), reverse=True)[:5]
    print(json.dumps({
        'passed': not errors,
        'scope': 'index' if args.index else args.revision,
        'file_count': len(files),
        'content_bytes': total,
        'content_mib': round(total / 1024**2, 3),
        'python_files_parsed': python_files,
        'largest_files': [{'path': name, 'bytes': len(files[name])} for name in largest],
        'errors': errors,
    }, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
