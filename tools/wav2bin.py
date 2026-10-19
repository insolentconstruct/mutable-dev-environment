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
"""Decode a Mutable Instruments audio firmware update (.wav) back to the binary.

Covers both encoders in stm_audio_bootloader, at any version:

  qpsk  Packets of 00*8 99*4 CC*4, payload, big-endian CRC32 of the payload as
        sent. The modulated signal starts at the first non-silent sample, where
        the carrier phase is 0, and blanks are whole bytes of symbol 0, so the
        symbol stream decodes byte-aligned from there. -k scrambling is undone.
  fsk   Square wave whose half-periods code 0, 1 and pause. Packets of 55*4,
        payload, big-endian CRC32.

Every packet CRC must match; any bad packet is an error. Pauses between pages
are not checked, because their length changed between encoder versions. The
output is the payloads in order, so it carries the encoder's page padding
(0xFF): compare it with a build using `--expect`, which allows only that tail.
"""
import argparse
import hashlib
import math
import sys
import wave
import zlib


def read_mono(path):
  with wave.open(path, 'rb') as w:
    if w.getsampwidth() != 2:
      raise SystemExit('%s: expected 16-bit PCM' % path)
    channels, rate = w.getnchannels(), w.getframerate()
    raw = w.readframes(w.getnframes())
  samples = memoryview(raw).cast('h')[::channels]
  return rate, samples


def packets(stream, preamble, payload_size):
  """Yield payloads following each preamble; every CRC must match."""
  i, n = 0, len(stream)
  while True:
    i = stream.find(preamble, i)
    if i < 0:
      return
    start = i + len(preamble)
    end = start + payload_size + 4
    if end > n:
      raise SystemExit('truncated packet at byte %d' % i)
    payload = stream[start:start + payload_size]
    crc = int.from_bytes(stream[start + payload_size:end], 'big')
    if zlib.crc32(payload) & 0xffffffff != crc:
      raise SystemExit('CRC mismatch in packet at byte %d' % i)
    yield payload
    i = end


def qpsk_bytes(rate, samples, carrier, baud):
  start = next((k for k, x in enumerate(samples) if x), None)
  if start is None:
    raise SystemExit('no signal')
  symbol_length = rate * 2 // baud
  nsym = (len(samples) - start) // symbol_length
  nsym -= nsym % 4
  w = 2 * math.pi * carrier / rate
  sin_t = [math.sin(w * k) for k in range(rate)]
  cos_t = [math.cos(w * k) for k in range(rate)]
  out = bytearray()
  byte = 0
  for s in range(nsym):
    q = i = 0.0
    base = s * symbol_length
    for k in range(base, base + symbol_length):
      x = samples[start + k]
      q += x * sin_t[k % rate]
      i += x * cos_t[k % rate]
    byte = (byte << 2) | ((i > 0) << 1) | (q > 0)
    if s % 4 == 3:
      out.append(byte)
      byte = 0
  return bytes(out)


def fsk_bytes(samples, zero, one, pause):
  bits, run, sign = [], 0, None
  def flush(run):
    # Classify a half-period by its nearest nominal length.
    d = min((abs(run - zero), 0), (abs(run - one), 1), (abs(run - pause), 2))
    bits.append(d[1])
  for x in samples:
    s = x > 0
    if x == 0 and sign is None:
      continue
    if s == sign:
      run += 1
    else:
      if sign is not None:
        flush(run)
      sign, run = s, 1
  flush(run)
  # Packets never contain pauses; decode each run of data bits separately.
  out, cur = bytearray(), []
  for b in bits + [2]:
    if b == 2:
      for k in range(0, len(cur) - 7, 8):
        out.append(int(''.join(map(str, cur[k:k + 8])), 2))
      cur = []
      out += b'\x00' * 8  # keeps packets apart in the byte stream
    else:
      cur.append(b)
  return bytes(out)


def descramble(data):
  out, state = bytearray(), 0
  for b in data:
    out.append(b ^ (state >> 24))
    state = (state * 1664525 + 1013904223) & 0xffffffff
  return bytes(out)


def main():
  p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  p.add_argument('wav')
  p.add_argument('--mode', choices=('qpsk', 'fsk'), required=True)
  p.add_argument('--carrier', type=int, default=6000, help='qpsk carrier, Hz')
  p.add_argument('--baud', type=int, default=12000, help='qpsk bit rate')
  p.add_argument('--fsk', default='4,8,16', help='fsk zero,one,pause half-periods in samples')
  p.add_argument('--packet-size', type=int, default=256)
  p.add_argument('--scrambled', action='store_true', help='qpsk -k files')
  p.add_argument('-o', '--output')
  p.add_argument('--expect', help='binary that must equal the decoded image up to 0xFF padding')
  a = p.parse_args()

  rate, samples = read_mono(a.wav)
  if a.mode == 'qpsk':
    stream = qpsk_bytes(rate, samples, a.carrier, a.baud)
    image = b''.join(packets(stream, b'\x00' * 8 + b'\x99' * 4 + b'\xcc' * 4, a.packet_size))
    if a.scrambled:
      image = descramble(image)
  else:
    zero, one, pause = map(int, a.fsk.split(','))
    stream = fsk_bytes(samples, zero, one, pause)
    image = b''.join(packets(stream, b'\x55' * 4, a.packet_size))
  if not image:
    raise SystemExit('no packets found')
  if a.output:
    with open(a.output, 'wb') as f:
      f.write(image)
  print('%d bytes, sha256 %s' % (len(image), hashlib.sha256(image).hexdigest()))
  if a.expect:
    with open(a.expect, 'rb') as f:
      ours = f.read()
    tail = image[len(ours):]
    if image[:len(ours)] != ours or tail.strip(b'\xff'):
      raise SystemExit('MISMATCH: decoded image differs from %s' % a.expect)
    print('MATCH: %s (%d bytes, %d bytes of padding)' % (a.expect, len(ours), len(tail)))


if __name__ == '__main__':
  main()
