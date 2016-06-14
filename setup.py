# -*- coding: utf-8 -*-

from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='kubelib',
    version='0.0.1',
    description='Utility wrapper around Kubectl',
    long_description=readme,
    author='Jason Kane',
    author_email='jkane@safaribooksonline.com',
    url='https://safaribooks.com',
    license=license,
    packages=find_packages(exclude=('tests', 'docs')),
    install_requires=[
        'bunch',
        'PyYaml',
        'sh',
    ]
)