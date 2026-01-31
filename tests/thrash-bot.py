#!/usr/bin/python3

import random

def random_bogus():
    word = ""
    letters=['s','a','p','e']
    for i in range(0,5):
        word += letters[random.getrandbits(2)]
    return word

my_goblus = {}

while True:
    word = random_bogus()
    my_goblus[word] = my_goblus.get(word," nothing here but soon ") + (word + "cucumbers and sausages are good together")*16000
