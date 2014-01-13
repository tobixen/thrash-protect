#!/usr/bin/python3

import random

def random_bogus():
    word = ""
    letters=['s','a','p','e']
    numl = len(letters)-1
    for i in range(0,5):
        word += letters[random.getrandbits(3)]
    return word

my_goblus = {}

while True:
    word = random_bogus()
    my_goblus[word] = (my_goblus.get(word," nothing here but soon ")[:9000] + "cucumbers and sausages are good together")*32
