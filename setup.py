import os
import re

from setuptools import setup

import shutil

long_description = """
The script attempts to detect thrashing situations and temporary stop rogue processes, 
hopefully before things get too much out of control, hopefully giving a sysadm enough time 
to investigate and handle the situation if there is a sysadm around, and if not - hopefully 
allowing boxes to become just slightly degraded instead of completely thrashed, all until the offending 
processes ends or the oom killer kicks in.

As of 2014-09, the development seems to have stagnated - for the very simple reason that 
it seems to work well enough for me.
"""

module = 'thrash_protect'
build = '_build'

basedir = os.path.dirname(os.path.abspath(__file__))
os.chdir(basedir)

if not os.path.exists(build):
    os.mkdir(build)

shutil.copy('thrash-protect.py', '%s/%s.py' % (build, module))

with open(os.path.join(basedir, 'thrash-protect.py')) as f:
    _moduletext = f.read()

def readmeta(fieldname):
    return re.search(r'__%s__\s*=\s*"(.*)"' % re.escape(fieldname), _moduletext).group(1).strip()

setup(
    name='thrash-protect',
    version=readmeta('version'),
    description='Simple-Stupid user-space program doing "kill -STOP" and "kill -CONT" to protect from thrashing',
    long_description=long_description.strip(),
    license='GPLv3+',
    url='https://github.com/tobixen/thrash-protect',

    author=readmeta('author'),
    author_email=readmeta('email'),

    package_dir={'': build},

    py_modules=[module],
    zip_safe=False,
    include_package_data=True,

    extras_require=dict(
        build=['twine', 'wheel', 'setuptools-git'],
        # test=['pytest', 'testfixtures', 'pytest-cov'],
    ),

    entry_points={
        "console_scripts": ['thrash-protect=%s:main' % module]
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Topic :: Utilities",
        "Topic :: System :: Software Distribution",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.5",
        "Programming Language :: Python :: 2.6",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.0",
        "Programming Language :: Python :: 3.1",
        "Programming Language :: Python :: 3.2",
        "Programming Language :: Python :: 3.3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
    ],
    # setup_requires=['pytest-runner'],
    # tests_require=['pytest-cov', 'pytest', 'testfixtures'],

)
os.chdir(basedir)
shutil.rmtree(build, ignore_errors=True)
