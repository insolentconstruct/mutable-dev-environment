#!/usr/bin/env python3
# Copyright 2026 Insolent Construct.
#
# Author: Insolent Construct (insolentconstruct@gmail.com)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
# See http://creativecommons.org/licenses/MIT/ for more information.
#
# -----------------------------------------------------------------------------
"""Build the STM32 modules with every ARM GCC in compilers/compilers.tsv.

  images  builds one image per compiler, <base>:gcc-<release>, on top of the
          mutable-dev image (compilers/Dockerfile; GCC 16 from source with
          compilers/gcc16.Dockerfile).
  run     exports the eurorack tree at a commit (with its submodules), builds
          each module's application in each compiler image with no network,
          and prints application flash use (text + data) per module and
          compiler. Firmware flags are unchanged; --no-werror only appends
          -Wno-error, to measure builds that newer warnings would stop.

Compilers run in parallel, one container per Docker context at a time
(--context may be repeated); modules build in parallel inside each container.
"""
import argparse
import concurrent.futures
import csv
import hashlib
import itertools
import json
import os
import queue
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
COMPILERS_DIR = os.path.join(os.path.dirname(HERE), 'compilers')
sys.path.insert(0, HERE)
from check_releases import docker, export_tree, git

FIELDS = ['release', 'module', 'status', 'text', 'data', 'bss', 'flash', 'flash_size', 'free',
          'warnings', 'bin_sha256', 'error']


def read_compilers():
  """(release, source, sha256) rows; source is a URL, path:DIR or file:DOCKERFILE."""
  rows = []
  with open(os.path.join(COMPILERS_DIR, 'compilers.tsv')) as f:
    for line in f:
      line = line.split('#')[0].strip()
      if line:
        rows.append(tuple(line.split()))
  return rows


def image_name(base, release):
  # Drop the tag, not a registry port.
  if ':' in base.rsplit('/', 1)[-1]:
    base = base.rsplit(':', 1)[0]
  return '%s:gcc-%s' % (base, release)


def build_images(a, compilers):
  for release, source, sha in compilers:
    args = ['build', '--platform', 'linux/amd64', '-t', image_name(a.base, release),
            '--build-arg', 'BASE=' + a.base, '--build-arg', 'GCC_RELEASE=' + release]
    if source.startswith('file:'):
      args += ['-f', os.path.join(COMPILERS_DIR, source[5:])]
    elif source.startswith('path:'):
      args += ['--build-arg', 'GCC_PATH=' + source[5:]]
    else:
      args += ['--build-arg', 'GCC_URL=' + source, '--build-arg', 'GCC_SHA256=' + sha]
    print('== %s' % image_name(a.base, release), flush=True)
    docker(a.context[0] if a.context else None, *(args + [COMPILERS_DIR]), check=True)


def stm32_modules(repo, commit):
  modules = []
  for name in git(repo, 'ls-tree', '--name-only', commit).decode().split():
    try:
      text = git(repo, 'show', '%s:%s/makefile' % (commit, name), stderr=subprocess.DEVNULL).decode()
    except subprocess.CalledProcessError:
      continue
    if 'stmlib/makefile.inc' in text:
      modules.append(name)
  return modules


def run_compiler(release, image, context, src_tar, modules, no_werror, logs):
  script = ('set -e; mkdir -p /tmp/src; cd /tmp/src; tar -xf /tmp/src.tar; '
            'find . -name "resources.cc" -o -name "resources.h" | xargs -r touch; '
            'python3 /tmp/compiler_matrix.py worker %s %s' %
            ('--no-werror' if no_werror else '', ' '.join(modules)))
  cid = docker(context, 'create', '--platform', 'linux/amd64', '--network', 'none', image,
               'bash', '-c', script, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
  try:
    docker(context, 'cp', src_tar, cid + ':/tmp/src.tar', check=True)
    for name in ('compiler_matrix.py', 'check_releases.py'):
      docker(context, 'cp', os.path.join(HERE, name), cid + ':/tmp/' + name, check=True)
    r = docker(context, 'start', '-a', cid, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if logs:
      dest = os.path.join(logs, release)
      os.makedirs(dest, exist_ok=True)
      docker(context, 'cp', cid + ':/tmp/logs/.', dest, stdout=subprocess.DEVNULL)
  finally:
    docker(context, 'rm', '-f', cid, stdout=subprocess.DEVNULL)
  rows = []
  for line in r.stdout.splitlines():
    if line.startswith('{'):
      row = json.loads(line)
      row['release'] = release
      rows.append(row)
  missing = set(modules) - set(row['module'] for row in rows)
  if r.returncode or missing:
    raise RuntimeError('%s: worker exit %d, no result for %s\n%s' % (
        release, r.returncode, ' '.join(sorted(missing)) or '-', r.stdout[-2000:]))
  return rows


def run(a, compilers):
  repo = os.path.abspath(a.repo)
  commit = git(repo, 'rev-parse', a.commit).decode().strip()
  modules = a.modules or stm32_modules(repo, commit)
  contexts = queue.Queue()
  for context in a.context or [None]:
    contexts.put(context)
  rows = []
  with tempfile.TemporaryDirectory() as tmp:
    src_tar = os.path.join(tmp, 'src.tar')
    with open(src_tar, 'wb') as f:
      f.write(export_tree(repo, commit))

    def job(compiler):
      context = contexts.get()
      try:
        release = compiler[0]
        rows = run_compiler(release, image_name(a.base, release), context, src_tar, modules,
                            a.no_werror, a.logs)
        print('done %-14s on %s' % (release, context or 'default'), file=sys.stderr, flush=True)
        return rows
      finally:
        contexts.put(context)

    with concurrent.futures.ThreadPoolExecutor(len(a.context or [None])) as pool:
      for result in pool.map(job, compilers):
        rows += result

  print('eurorack %s%s; application flash bytes (text + data), free bytes in brackets' %
        (commit[:12], ', -Wno-error' if a.no_werror else ''))
  releases = [c[0] for c in compilers]
  cells = dict(((r['release'], r['module']), r) for r in rows)
  width = max(len(r) for r in releases + ['module']) + 1
  print('%-9s' % 'module' + ''.join('%*s' % (width + 9, r) for r in releases))
  for module in modules:
    line = '%-9s' % module
    for release in releases:
      r = cells.get((release, module))
      if not r:
        cell = '-'
      elif r['status'] != 'ok':
        cell = 'FAIL ' + r['status']
      else:
        cell = '%d [%d]' % (r['flash'], r['free'])
      line += '%*s' % (width + 9, cell)
    print(line)
  if a.csv:
    with open(a.csv, 'w', newline='') as f:
      w = csv.DictWriter(f, FIELDS)
      w.writeheader()
      for r in sorted(rows, key=lambda r: (releases.index(r['release']), r['module'])):
        w.writerow(dict((k, r.get(k, '')) for k in FIELDS))


# Runs inside a compiler image, in the exported tree.

def build_module(module, no_werror):
  makefile = '%s/makefile' % module
  target = re.search(r'^TARGET\s*=\s*(\S+)', open(makefile).read(), re.M).group(1)
  if no_werror:
    makefile = '/tmp/%s-measure.mk' % module
    with open(makefile, 'w') as f:
      f.write('include %s/makefile\nCCFLAGS += -Wno-error\n' % module)
  build_root = '/tmp/build/%s/' % module
  r = subprocess.run(['make', '-f', makefile, 'BUILD_ROOT=' + build_root, 'bin'],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
  os.makedirs('/tmp/logs', exist_ok=True)
  with open('/tmp/logs/%s.log' % module, 'w') as f:
    f.write(r.stdout)
  row = {'module': module, 'warnings': len(re.findall(r'\bwarning:', r.stdout))}
  stem = '%s%s/%s' % (build_root, target, target)
  if r.returncode == 0:
    row['status'] = 'ok'
    size = subprocess.run([os.environ.get('TOOLCHAIN_PATH', '/usr/local/arm/') + 'bin/arm-none-eabi-size',
                           stem + '.elf'], check=True, stdout=subprocess.PIPE, text=True).stdout
    text, data, bss = map(int, size.splitlines()[1].split()[:3])
    row.update(text=text, data=data, bss=bss, flash=text + data)
    with open(stem + '.bin', 'rb') as f:
      row['bin_sha256'] = hashlib.sha256(f.read()).hexdigest()
    # The application's flash region, from the linker map's memory configuration.
    memory = open(stem + '.map').read().split('Memory Configuration', 1)[1]
    region = re.search(r'^\s*flash\s+0x[0-9a-f]+\s+0x([0-9a-f]+)', memory, re.M | re.I)
    if region:
      row['flash_size'] = int(region.group(1), 16)
      row['free'] = row['flash_size'] - row['flash']
  else:
    linking = re.search(r'collect2|\bld(\.bfd)?: |region .* overflowed', r.stdout)
    row['status'] = 'link' if linking else 'compile'
    errors = [l for l in r.stdout.splitlines() if 'error' in l.lower()]
    row['error'] = (errors or r.stdout.splitlines()[-1:] or [''])[0][:300]
  return row


def worker(a):
  def job(module):
    try:
      return build_module(module, a.no_werror)
    except Exception as e:
      return {'module': module, 'status': 'error', 'error': repr(e)[:300]}

  with concurrent.futures.ThreadPoolExecutor(os.cpu_count()) as pool:
    for row in pool.map(job, a.modules):
      print(json.dumps(row), flush=True)


def main():
  p = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                              formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
  sub = p.add_subparsers(dest='command', required=True)
  for name in ('images', 'run'):
    s = sub.add_parser(name)
    s.add_argument('--base', default=os.environ.get('MUTABLE_IMAGE', 'mutable-dev'))
    s.add_argument('--context', action='append', help='docker context (repeat to spread compilers)')
    s.add_argument('--compiler', action='append', help='release name from compilers.tsv (default: all)')
  s = sub.choices['run']
  s.add_argument('--repo', default='.', help='eurorack checkout with submodules')
  s.add_argument('--commit', default='HEAD')
  s.add_argument('--no-werror', action='store_true', help='append -Wno-error (measurement only)')
  s.add_argument('--csv', help='write every result to this file')
  s.add_argument('--logs', help='copy build logs to DIR/<release>/<module>.log')
  s.add_argument('modules', nargs='*', help='default: every module built with stmlib')
  s = sub.add_parser('worker')
  s.add_argument('--no-werror', action='store_true')
  s.add_argument('modules', nargs='+')
  a = p.parse_args()
  if a.command == 'worker':
    worker(a)
    return
  compilers = read_compilers()
  if a.compiler:
    unknown = set(a.compiler) - set(c[0] for c in compilers)
    if unknown:
      p.error('not in compilers.tsv: ' + ' '.join(sorted(unknown)))
    compilers = [c for c in compilers if c[0] in a.compiler]
  if a.command == 'images':
    build_images(a, compilers)
  else:
    run(a, compilers)


if __name__ == '__main__':
  main()
