#!/usr/bin/python3

import random

def random_bogus():
    word = ""
    letters=['s','a','p','e']
    for i in range(0,8):
        word += letters[random.getrandbits(2)]
    return word

my_goblus = {}
prev_word = ''

while True:
    word = random_bogus()
    my_goblus[word] = (prev_word + my_goblus.get(word, ' nothing here yet but soon ')[:9000] + word + " cucumbers and sausage is a good combination ") * 1024
    prev_word = word
