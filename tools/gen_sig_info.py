#!/usr/bin/env python3
# Copyright 2023 The Emscripten Authors.  All rights reserved.
# Emscripten is available under two separate licenses, the MIT license and the
# University of Illinois/NCSA Open Source License.  Both these licenses can be
# found in the LICENSE file.

"""This tool extracts native/C signature information for JS library functions

It generates a file called `src/library_sigs.js` which contains `__sig` declarations
for the majority of JS library functions.
"""

import argparse
import json
import os
import sys
import subprocess
import re
import glob


__scriptdir__ = os.path.dirname(os.path.abspath(__file__))
__rootdir__ = os.path.dirname(__scriptdir__)
sys.path.append(__rootdir__)

from tools import shared, utils, webassembly

header = '''/* Auto-generated by %s */

#define _GNU_SOURCE

// Public emscripen headers
#include <emscripten/emscripten.h>
#include <emscripten/heap.h>
#include <emscripten/console.h>
#include <emscripten/em_math.h>
#include <emscripten/html5.h>
#include <emscripten/fiber.h>
#include <emscripten/websocket.h>
#include <emscripten/webaudio.h>
#include <wasi/api.h>

// Internal emscripten headers
#include "emscripten_internal.h"
#include "webgl_internal.h"

// Internal musl headers
#include "musl/include/assert.h"
#include "musl/arch/emscripten/syscall_arch.h"
#include "dynlink.h"

// Public musl/libc headers
#include <cxxabi.h>
#include <unwind.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netdb.h>
#include <time.h>
#include <unistd.h>
#include <dlfcn.h>

// Public library headers
#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glut.h>
#include <GL/glew.h>
#include <AL/al.h>
#include <AL/alc.h>
#include <SDL/SDL.h>
#include <SDL/SDL_mutex.h>
#include <SDL/SDL_image.h>
#include <SDL/SDL_mixer.h>
#include <SDL/SDL_surface.h>
#include <SDL/SDL_ttf.h>
#include <SDL/SDL_gfxPrimitives.h>
#include <SDL/SDL_rotozoom.h>
#include <webgl/webgl1_ext.h>
#include <webgl/webgl2_ext.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <uuid/uuid.h>
''' % os.path.basename(__file__)

footer = '''\
};

int main(int argc, char* argv[]) {
  return argc + (intptr_t)symbol_list;
}
'''

wasi_symbols = {
  'proc_exit',
  'environ_sizes_get',
  'environ_get',
  'clock_time_get',
  'clock_res_get',
  'fd_write',
  'fd_pwrite',
  'fd_read',
  'fd_pread',
  'fd_close',
  'fd_seek',
  'fd_sync',
  'fd_fdstat_get',
  'args_get',
  'args_sizes_get',
}


def ignore_symbol(s):
  if s in {'SDL_GetKeyState'}:
    return True
  if s.startswith('emscripten_gl') or s.startswith('emscripten_alc'):
    return True
  if s.startswith('egl'):
    return True
  if s.startswith('gl') and any(s.endswith(x) for x in ('NV', 'EXT', 'WEBGL', 'ARB', 'ANGLE')):
    return True
  return False


def create_c_file(filename, symbol_list):
  source_lines = [header]
  source_lines.append('\nvoid* symbol_list[] = {')
  for s in symbol_list:
    if s in wasi_symbols:
      source_lines.append(f'  &__wasi_{s},')
    else:
      source_lines.append(f'  &{s},')
  source_lines.append(footer)
  utils.write_file(filename, '\n'.join(source_lines) + '\n')


def valuetype_to_chr(t, t64):
  if t == webassembly.Type.I32 and t64 == webassembly.Type.I64:
    return 'p'
  assert t == t64
  return {
    webassembly.Type.I32: 'i',
    webassembly.Type.I64: 'j',
    webassembly.Type.F32: 'f',
    webassembly.Type.F64: 'd',
  }[t]


def functype_to_str(t, t64):
  assert len(t.returns) == len(t64.returns)
  assert len(t.params) == len(t64.params)
  if t.returns:
    assert len(t.returns) == 1
    rtn = valuetype_to_chr(t.returns[0], t64.returns[0])
  else:
    rtn = 'v'
  for p, p64 in zip(t.params, t64.params):
    rtn += valuetype_to_chr(p, p64)
  return rtn


def write_sig_library(filename, sig_info):
  lines = [
      '/* Auto-generated by tools/gen_sig_info.py. DO NOT EDIT. */',
      '',
      'sigs = {'
  ]
  for s, sig in sorted(sig_info.items()):
    lines.append(f"  {s}__sig: '{sig}',")
  lines += [
      '}',
      '',
      '// We have to merge with `allowMissing` since this file contains signatures',
      '// for functions that might not exist in all build configurations.',
      'mergeInto(LibraryManager.library, sigs, {allowMissing: true});'
  ]
  utils.write_file(filename, '\n'.join(lines) + '\n')


def update_sigs(sig_info):
  print("updating __sig attributes ...")

  def update_line(l):
    if '__sig' not in l:
      return l
    stripped = l.strip()
    for sym, sig in sig_info.items():
      if stripped.startswith(f'{sym}__sig:'):
        return re.sub(rf"\b{sym}__sig: '.*'", f"{sym}__sig: '{sig}'", l)
    return l

  files = glob.glob('src/*.js') + glob.glob('src/**/*.js')
  for file in files:
    lines = utils.read_file(file).splitlines()
    lines = [update_line(l) for l in lines]
    utils.write_file(file, '\n'.join(lines) + '\n')


def remove_sigs(sig_info):
  print("removing __sig attributes ...")

  to_remove = [f'{sym}__sig:' for sym in sig_info.keys()]

  def strip_line(l):
    l = l.strip()
    return any(l.startswith(r) for r in to_remove)

  files = glob.glob('src/*.js') + glob.glob('src/**/*.js')
  for file in files:
    lines = utils.read_file(file).splitlines()
    lines = [l for l in lines if not strip_line(l)]
    utils.write_file(file, '\n'.join(lines) + '\n')


def extract_sigs(symbols, obj_file):
  sig_info = {}
  with webassembly.Module(obj_file) as mod:
    imports = mod.get_imports()
    types = mod.get_types()
    import_map = {i.field: i for i in imports}
    for s in symbols:
      sig_info[s] = types[import_map[s].type]
  return sig_info


def extract_sig_info(sig_info, extra_settings=None, extra_cflags=None):
  tempfiles = shared.get_temp_files()
  settings = {
    'USE_PTHREADS': 1,
    'STACK_OVERFLOW_CHECK': 1,
    'FULL_ES3': 1,
    'USE_SDL': 1,
    # Currently GLFW symbols have different sigs for the same symbol because the
    # signatures changed between v2 and v3, so for now we continue to maintain
    # them by hand.
    'USE_GLFW': 0,
    'JS_LIBRARIES': ['src/library_websocket.js',
                     'src/library_webaudio.js'],
    'SUPPORT_LONGJMP': 'emscripten'
  }
  if extra_settings:
    settings.update(extra_settings)
  with tempfiles.get_file('.json') as settings_json:
    utils.write_file(settings_json, json.dumps(settings))
    output = shared.run_js_tool(utils.path_from_root('src/compiler.js'),
                                ['--symbols-only', settings_json],
                                stdout=subprocess.PIPE, cwd=utils.path_from_root())
  symbols = json.loads(output).keys()
  symbols = [s for s in symbols if not ignore_symbol(s)]
  with tempfiles.get_file('.c') as c_file:
    create_c_file(c_file, symbols)

    # We build the `.c` file twice, once with wasm32 and wasm64.
    # The first build gives is that base signature of each function.
    # The second build build allows us to determine which args/returns are pointers
    # or `size_t` types.  These get marked as `p` in the `__sig`.
    obj_file = 'out.o'
    cmd = [shared.EMCC, c_file, '-c', '-pthread',
           '-Wno-deprecated-declarations',
           '-o', obj_file,
           '-I' + utils.path_from_root('system/lib/libc'),
           '-I' + utils.path_from_root('system/lib/libc/musl/src/include'),
           '-I' + utils.path_from_root('system/lib/libc/musl/src/internal'),
           '-I' + utils.path_from_root('system/lib/gl'),
           '-I' + utils.path_from_root('system/lib/libcxxabi/include'),
           '-isysroot', 'c++/v1']
    if extra_cflags:
      cmd += extra_cflags
    shared.check_call(cmd)
    sig_info32 = extract_sigs(symbols, obj_file)

    # Run the same command again with memory64.
    shared.check_call(cmd + ['-sMEMORY64', '-Wno-experimental'])
    sig_info64 = extract_sigs(symbols, obj_file)

    for sym, sig32 in sig_info32.items():
      assert sym in sig_info64
      sig64 = sig_info64[sym]
      sig_string = functype_to_str(sig32, sig64)
      if sym in sig_info:
        if sig_info[sym] != sig_string:
          print(sym)
          print(sig_string)
          print(sig_info[sym])
          assert sig_info[sym] == sig_string
      sig_info[sym] = sig_string


def main(args):
  parser = argparse.ArgumentParser()
  parser.add_argument('-o', '--output', default='src/library_sigs.js')
  parser.add_argument('-r', '--remove', action='store_true', help='remove from JS library files any `__sig` entires that are part of the auto-generated file')
  parser.add_argument('-u', '--update', action='store_true', help='update with JS library files any `__sig` entires that are part of the auto-generated file')
  args = parser.parse_args()

  print('generating signatures ...')
  sig_info = {}
  extract_sig_info(sig_info)
  extract_sig_info(sig_info, {'STANDALONE_WASM': 1})
  extract_sig_info(sig_info, {'MAIN_MODULE': 2})

  write_sig_library(args.output, sig_info)
  if args.update:
    update_sigs(sig_info)
  if args.remove:
    remove_sigs(sig_info)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
