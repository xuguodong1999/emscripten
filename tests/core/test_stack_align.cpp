// Copyright 2014 The Emscripten Authors.  All rights reserved.
// Emscripten is available under two separate licenses, the MIT license and the
// University of Illinois/NCSA Open Source License.  Both these licenses can be
// found in the LICENSE file.

#include <stdio.h>

#define ALIGN(num_bytes) __attribute__((aligned(num_bytes)))

struct Aligned {
  char ALIGN(4) a4;
  char ALIGN(8) a8;
  char ALIGN(16) a16;
  char ALIGN(32) a32;
};

__attribute__((noinline))
void Test(const void* p, int size) {
  printf("align %d: %zu\n", size, reinterpret_cast<size_t>(p) % size);
}

int main() {
  Aligned a;
  Test(&a.a4, 4);
  Test(&a.a8, 8);
  Test(&a.a16, 16);
  Test(&a.a32, 32);

  int p = reinterpret_cast<size_t>(&a);
  printf("base align: %d, %d, %d, %d\n", p%4, p%8, p%16, p%32);

  return 0;
}

