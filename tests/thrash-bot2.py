import os
import array
import argparse


def alloc(megs, filename):
    res = array.array('b')
    for i in range(megs):
        res.fromfile(open('/tmp/bigfile', 'rb'), 1048576)
    return res

fn = '/tmp/bigfile.%s' % os.getpid()
parser = argparse.ArgumentParser()
parser.add_argument("--alloc-start", help="start by allocating this amount of memory (should be a bit less than what's available)", default=3000, type=int)
args = parser.parse_args()
i = args.alloc_start
try:
    os.system('dd if=/dev/zero of=%s bs=1M count=1' % fn)
    while True:
        i+=1
        print("alloc %d" % i)
        alloc(i, fn)
finally:
    os.unlink(fn)
    
