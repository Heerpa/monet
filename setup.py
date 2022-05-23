import os
import re
from setuptools import setup, find_packages


# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name="monet",
    version='0.0.0',
    author="Heinrich Grabmayr",
    author_email="hgrabmayr@biochem.mpg.de",
    description=("A python based software suite to calibrate " +
                 "laser power."),
    license="BSD",
    keywords="laser power calibration",
    # url=get_url(),
    entry_points={'console_scripts':
                  ['monet = monet.__main__:main']},
    include_package_data=True,
    # packages=find_packages(exclude=['tests']),
    packages=find_packages(),
    long_description=read('README.md'),
    classifiers=[
        "Development Status :: 3 - Alpha",
        # "Topic :: Utilities",
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.8",
        "Operating System :: OS Independent"
    ],
)
