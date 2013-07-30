#!/usr/bin/python3

import random

def random_bogus():
    word = ""
    letters=['s','a','p','e']
    numl = len(letters)-1
    for i in range(0,5):
        word += letters[random.randint(0,numl)]
    return word

my_count = {}
my_goblus = {}
prev_word = ''
foobar = 0

while True:
    word = random_bogus()
    num = my_count.get(word, 0)
    if (num % (foobar+100)) == (foobar+99):
        print("%s: %s %s" % (word, num, len(my_goblus.get(word,""))))
        foobar += 1
    my_count[word] = num + 1
    my_goblus[word] = my_goblus.get(word, 'jalla') + prev_word * (num + 10000)
    prev_word = word
    prev_num = num
