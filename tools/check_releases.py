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
"""Rebuild Mutable Instruments' released firmware in the image and compare it
with the published update files, byte for byte.

For each release below: export the eurorack tree at the release commit (with
the stmlib and stm_audio_bootloader submodules at their recorded commits), run
`make -f <module>/makefile bin` in a fresh container with no network, decode
the published .wav with tools/wav2bin.py using the encoder settings from that
commit's makefile, and require the decoded image to equal the build.
"""
import argparse
import hashlib
import io
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

DOCS_URL = 'https://raw.githubusercontent.com/pichenettes/mutable-instruments-documentation/master/docs/modules/'

# module, version, eurorack commit, update file under docs/modules/, sha256 of that file.
RELEASES = [
  ('braids', '1.8', '8d26baef1659b5dfcc6d1775c1a7652987b0a5d7', 'braids/downloads/braids_1.8.wav',
   'ef731f7d408ef667b2dd8b7a09870a2fcffb23e6f4a57166d126d97a79bb1091'),
  ('clouds', '1.31', '1b3b32edafcd9cbb2b6972bdebbba39323deec49', 'clouds/downloads/clouds_1.31.wav',
   'abd00570d6f4bc5f605941696e737c40b8a9f94ea5038f25ab63eaac15c46bd1'),
  ('elements', '1.1', 'ff9dd11a2abd7d1262b05c603c2a21fd3069283e', 'elements/downloads/elements_1.1.wav',
   'b3886332638f42975187d76288cec2f26caa4ac07003e12767c262b06ff6e23c'),
  ('marbles', '1.3', 'db1566f68db7880baa569640e00f3854ab459adb', 'marbles/downloads/marbles_1.3.wav',
   '1da37cdb26aee8f48fffe1dea4de723dffa7639ccda5d53af6995e699e485383'),
  ('peaks', '1.1', '26f0ec7a60b94b4c73b6cd04eb786cbf0d42b7ef', 'peaks/downloads/peaks_1.1.wav',
   'ac5fa55fa73042be695134d95566b13263ecfdf19ed385d8dc7d16ee2c522e00'),
  ('stages', '1.2', '897806facd8fd9f696585a4b83ec81d9e900f5a2', 'stages/downloads/stages_1.2.wav',
   '2006e782b34cac922bf0e55cf30c82dc590934da535f20da6181c9d91fe971f5'),
  ('tides', '1.1', '3b83a16e06a9a4695b0e024f5c890b54db21c1ac', 'tides_original/downloads/tides_1.1.wav',
   'fd33053339d4fbe884ffb5443d3a7a4148da0471e8ffffdb9dd952971b2e46fe'),
  ('tides2', '1.2', 'd1e057173cd00811c827c7a53c4f6f1907af7327', 'tides_2018/downloads/tides_1.2.wav',
   '2f0d331139288721e5c933f30e589b088857fdb8b0c28ae39e76673a83c5f4be'),
]

SUBMODULES = ('stmlib', 'stm_audio_bootloader')
HERE = os.path.dirname(os.path.abspath(__file__))


def git(repo, *args, **kw):
  return subprocess.run(['git', '-C', repo] + list(args), check=True, stdout=subprocess.PIPE, **kw).stdout


def export_tree(repo, commit):
  """The tree at commit plus its submodules at their gitlinks, as tar bytes."""
  out = io.BytesIO()
  with tarfile.open(fileobj=out, mode='w') as dst:
    def add(tar_bytes, prefix=''):
      with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as src:
        for member in src:
          if member.name == 'pax_global_header':
            continue
          member.name = prefix + member.name
          dst.addfile(member, src.extractfile(member) if member.isfile() else None)
    add(git(repo, 'archive', '--format=tar', commit))
    for sub in SUBMODULES:
      line = git(repo, 'ls-tree', commit, sub).decode().split()
      if len(line) == 4 and line[1] == 'commit':
        add(git(os.path.join(repo, sub), 'archive', '--format=tar', line[2]), sub + '/')
  return out.getvalue()


def makefile_settings(repo, commit, module):
  text = git(repo, 'show', '%s:%s/makefile' % (commit, module)).decode()
  target = re.search(r'^TARGET\s*=\s*(\S+)', text, re.M).group(1)
  # The `wav` rule's recipe; Stages also keeps an older `wav_old` rule.
  wav = re.search(r'^wav\s*:.*$', text, re.M)
  rule = re.compile(r'stm_audio_bootloader/(qpsk|fsk)/encoder\.py((?:[^\n]*\\\n)*[^\n]*)').search(text, wav.end())
  mode, args = rule.group(1), shlex.split(rule.group(2).replace('\\\n', ' '))
  opts = dict(zip(args, args[1:]))
  if mode == 'qpsk':
    flags = ['--mode', 'qpsk', '--carrier', opts.get('-c', '6000'),
             '--baud', opts.get('-b', '12000'), '--packet-size', opts.get('-p', '256')]
    if '-k' in args:
      flags.append('--scrambled')
  else:
    # fsk encoder: -b pause, -n one, -z zero half-periods (defaults 64, 16, 8).
    half_periods = (opts.get('-z', '8'), opts.get('-n', '16'), opts.get('-b', '64'))
    flags = ['--mode', 'fsk', '--fsk', '%s,%s,%s' % half_periods,
             '--packet-size', opts.get('-p', '256')]
  return target, flags


def release_file(rel_path, sha, docs):
  if docs:
    with open(os.path.join(docs, 'docs', 'modules', rel_path), 'rb') as f:
      data = f.read()
  else:
    with urllib.request.urlopen(DOCS_URL + rel_path, timeout=120) as r:
      data = r.read()
  digest = hashlib.sha256(data).hexdigest()
  if digest != sha:
    raise SystemExit('%s: sha256 %s, expected %s' % (rel_path, digest, sha))
  return data


def docker(context, *args, **kw):
  base = ['docker'] + (['--context', context] if context else [])
  return subprocess.run(base + list(args), **kw)


def check(release, repo, docs, image, context):
  module, version, commit, rel_path, sha = release
  target, flags = makefile_settings(repo, commit, module)
  wav = release_file(rel_path, sha, docs)
  # Touch the committed resource tables so make never regenerates them: releases
  # are built from the files Mutable Instruments shipped, and need no Python.
  script = ('set -e; mkdir -p /tmp/src; cd /tmp/src; tar -xf /tmp/src.tar; '
            'find . -name "resources.cc" -o -name "resources.h" | xargs -r touch; '
            'make -f {m}/makefile bin > /tmp/build.log 2>&1 || {{ tail -30 /tmp/build.log; exit 1; }}; '
            'python3 /tmp/wav2bin.py /tmp/release.wav {flags} --expect build/{t}/{t}.bin'
            ).format(m=module, t=target, flags=' '.join(map(shlex.quote, flags)))
  cid = docker(context, 'create', '--platform', 'linux/amd64', '--network', 'none', image,
               'bash', '-c', script, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
  try:
    with tempfile.TemporaryDirectory() as tmp:
      for name, data in (('src.tar', export_tree(repo, commit)), ('release.wav', wav)):
        with open(os.path.join(tmp, name), 'wb') as f:
          f.write(data)
        docker(context, 'cp', os.path.join(tmp, name), '%s:/tmp/%s' % (cid, name), check=True)
    docker(context, 'cp', os.path.join(HERE, 'wav2bin.py'), cid + ':/tmp/wav2bin.py', check=True)
    r = docker(context, 'start', '-a', cid, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
  finally:
    docker(context, 'rm', '-f', cid, stdout=subprocess.DEVNULL)
  return r.returncode == 0, r.stdout.strip().splitlines()


def main():
  p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  p.add_argument('--repo', default='.', help='eurorack checkout with full history and submodules')
  p.add_argument('--docs', help='local mutable-instruments-documentation checkout (default: download)')
  p.add_argument('--image', default='mutable-dev')
  p.add_argument('--context', help='docker context to build in')
  p.add_argument('modules', nargs='*', help='subset of: ' + ' '.join(r[0] for r in RELEASES))
  a = p.parse_args()
  unknown = set(a.modules) - set(r[0] for r in RELEASES)
  if unknown:
    p.error('no release check for: ' + ' '.join(sorted(unknown)))
  selected = [r for r in RELEASES if not a.modules or r[0] in a.modules]
  failed = 0
  for release in selected:
    ok, lines = check(release, os.path.abspath(a.repo), a.docs, a.image, a.context)
    failed += not ok
    print('%-8s %-5s %s  %s' % (release[0], release[1], 'OK  ' if ok else 'FAIL', lines[-1] if lines else ''))
    if not ok:
      for line in lines[:-1]:
        print('    ' + line)
  print('%d of %d releases reproduced' % (len(selected) - failed, len(selected)))
  sys.exit(1 if failed else 0)


if __name__ == '__main__':
  main()
