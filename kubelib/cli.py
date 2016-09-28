#!/usr/bin/python
"""Command line utilities"""

import hashlib
import re
import sys
import kubelib
import glob
import os

import docopt

import logging, logging.config

logging.config.dictConfig({
    'version': 1,
    'formatters': {
        'detailed': {
            'class': 'logging.Formatter',
            'format': '%(asctime)s %(name)-15s %(levelname)-8s %(processName)-10s %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'detailed'
        }
    },
    'loggers': {
        'kubelib': {
            'level': 'DEBUG',
            'propagate': True
        },
        'sh': {
            'level': 'WARNING',
            'propagate': True
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console']
    }
})

LOG = logging.getLogger(__name__)

# must be a DNS label (at most 63 characters, matching regex
# [a-z0-9]([-a-z0-9]*[a-z0-9])?): e.g. "my-name"
allowed_first_re = re.compile(r"^[a-z0-9]$")
allowed_re = re.compile(r"^[-a-z0-9]$")
passing_re = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

PREFIX = ""
SUFFIX = ["-kube", "-master", "-kubernetes"]


class InvalidBranch(Exception):
    """Simple failure exception class."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)

def add_prefix(namespace):
    """Derp, add the prefix."""
    if PREFIX:
        return PREFIX + "-" + namespace
    else:
        return namespace

def fix_length(branch):
    """Make sure the branch name is not too long."""
    if branch == "":
        raise InvalidBranch(branch)

    if len(branch) < (63 - len(PREFIX) - 1):
        # quick finish if the branch name is already a valid
        # docker namespace name
        return add_prefix(branch)
    else:
        # too long, truncate but add a bit-o-hash to increase the
        # odds that we're still as unique as the branch name
        branch_hash = hashlib.sha256(branch).hexdigest()
        # prefix it, cut it at the 60th character and add 3
        # characters of the hashed 'full' branch name.  Even
        # if you take a long branch and add -v2, you'll get
        # a unique but reproducable namespace.
        branch = add_prefix(branch)[:60] + branch_hash[:3]
        return branch

def _make_namespace(branch=None):
    """
    Take the branch name and return the docker namespace.

    some invalid branch names
    >>> create_namespace_name('')
    Traceback (most recent call last):
        ...
    InvalidBranch: ''

    >>> create_namespace_name('-this')
    'jenkins-this'

    >>> create_namespace_name('and_this')
    'jenkins-andthis'

    # some valid ones
    >>> create_namespace_name('and-this')
    'jenkins-and-this'

    >>> create_namespace_name('andthis')
    'jenkins-andthis'

    >>> create_namespace_name('AnDtHiS')
    'jenkins-andthis'

    >>> create_namespace_name('How-Now_Brown_Cow')
    'jenkins-how-nowbrowncow'

    >>> create_namespace_name('abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz0123456789')
    'jenkins-abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnop5f8'

    """
    if branch is None:
        branch = sys.argv[1]

    branch = branch.lower()

    # remove a
    as_list = branch.split('-')
    if as_list:
        # I am sorry.
        branch = '-'.join(
            [element for index, element in enumerate(as_list) if as_list.index(element) == index]
        )

    if not passing_re.match(branch):
        name = ""
        try:
            if allowed_first_re.match(branch[0]):
                name += branch[0]
        except IndexError:
            raise InvalidBranch(branch)

        for c in branch[1:]:
            if allowed_re.match(c):
                name += c
        branch = name

    for suffix in SUFFIX:
        if branch.endswith(suffix):
            branch = branch[:-1 * len(suffix)]

    branch = fix_length(branch)
    return(branch)

def make_namespace(branch=None):
    ns = _make_namespace(branch)
    print(ns)
    return(0)

def _make_nodeport(namespace=None):
    """
    Take the namespace and hash to a port.

    valid port numbers are the range (30000-32768)
    we want to take a namespace and turn it into a port
    number in a reproducable way with reasonably good
    distribution / low odds of hitting an already used port

    >>> create_nodeport_value('abc')
    32767

    >>> create_nodeport_value('abcdef')
    32405

    """
    # grab 10^4 bits pseudo-entropy and mod them into 30000-32768
    # 10 gives us a 3x bigger number than we actually need.
    if namespace is None:
        namespace = sys.argv[1]

    hash_val = int(hashlib.sha256(namespace).hexdigest()[0:10], 16)
    port = 30000 + (hash_val % 2768)  # 2768 = 32768 - 30000

    return port

def make_nodeport(namespace=None):
    np = _make_nodeport(namespace)
    print(np)
    return(0)

def wait_for_pod():
    """
    Wait for the given pod to be running

    Usage:
      wait_for_pod --namespace=<namespace> --pod=<pod> [--context=<context>] [--maxdelay=<maxdelay>]

    Options:
        -h --help               Show this screen
        --context=<context>     kube context [default: dev-seb]
        --namespace=<namespace> kubernetes namespace
        --pod=<pod>             Pod we want to wait for
        --maxdelay=<maxdelay>   Maximum time to wait in seconds [default: 300]
    """
    args = docopt.docopt(wait_for_pod.__doc__)
    LOG.debug(args)

    kube = kubelib.KubeConfig(
        context=args['--context'],
        namespace=args['--namespace']
    )

    pod = kubelib.Pod(kube).wait_for_pod(
        pod_name=args['--pod'],
        max_delay=float(args['--maxdelay'])
    )

    print(pod)
    return(0)

def envdir_to_configmap():
    """
    Convert a given envdir to a kubernetes configmap

    Usage:
      envdir_to_configmap --namespace=<namespace> [--context=<context>] envdir=<envdir>

    Options:
        -h --help                 Show this screen
        --context=<context>       kube context [default: dev-seb]
        --namespace=<namespace>   kubernetes namespace
        --configname=<configname> name of configmap
        --envdir=<envdir>         envdir directory
    """
    args = docopt.docopt(wait_for_pod.__doc__)
    config = {}
    for filename in glob.glob(os.path.join(args['--envdir'], "*")):
        with open(filename, 'r') as h:
            config[os.path.basename] = h.read()

    kube = kubelib.KubeConfig(
        context=args['--context'],
        namespace=args['--namespace']
    )

    kubelib.ConfigMap(kube).from_dict(
        args['configmap'], config
    )
    return(0)


if __name__ == "__main__":
    import doctest
    doctest.testmod()
