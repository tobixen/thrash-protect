import os
os.system('dd if=/dev/zero of=/tmp/bigfile bs=1M count=1')



import array

def alloc(megs):
    res = array.array('c')
    for i in range(megs):
        res.fromfile(open('/tmp/bigfile', 'rb'), 1048576)
    return res

i=5900
while True:
    i+=1
    print("alloc %d" % i)
    alloc(i)
