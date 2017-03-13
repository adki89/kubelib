#!/usr/bin/python
"""Command line utilities."""

import hashlib
import re
import sys
import kubelib
import glob
import os
import sh
# import colorama

try:
    from kubelib.tableview import TableView
except ImportError:
    from .tableview import TableView

import docopt

import logging
import logging.config

logging.config.dictConfig({
    'version': 1,
    'formatters': {
        'detailed': {
            'class': 'logging.Formatter',
            'format': '%(asctime)s %(name)-15s %(levelname)-8s'
                      ' %(processName)-10s %(message)s'
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
SUFFIX = ["-kube", "-master", "-kubernetes", "-"]


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
        branch_hash = hashlib.sha256(branch.encode('ascii')).hexdigest()
        # prefix it, cut it at the 60th character and add 3
        # characters of the hashed 'full' branch name.  Even
        # if you take a long branch and add -v2, you'll get
        # a unique but reproducable namespace.
        branch = add_prefix(branch)[:60] + branch_hash[:3]
        return branch


def _make_namespace(branch=None, branch_tag=None):
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

    if branch_tag is None and len(sys.argv) == 3:
        # new style, arg for the branch tag
        branch_tag = sys.argv[2]
        branch_tag = branch_tag.replace('-', '')
        branch = branch.replace('-', '')

        branch = branch + "-" + branch_tag
    elif branch_tag:
        branch = branch + "-" + branch_tag

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
    Wait for the given pod to be running.

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


def _get_namespace_limits(kube, namespace):
    ns = kubelib.Namespace(kube).get(namespace)
    # print(repr(ns))
    if hasattr(ns.status, "phase"):
        if ns.status.phase == 'Terminating':
            LOG.info(
                'Skipping %s (phase is %s)', ns.metadata.name, ns.status.phase
            )
            return []
    else:
        LOG.error('Failed to retrieve limits from namespace %s', namespace)
        return []

    return kubelib.LimitRange(kube).get_list()

UNITS = {
    'm': 1000000,
    'Mi': 1000000,
    'M': 1000000,
    'Gi': 1000000000,
    'Ti': 1000000000000,
    '': 1000000000
}

def as_value(mystr):
    numext = re.compile(r"([0-9]*)(.*)")
    num, unit = numext.match(mystr).groups()
    return float(num) * UNITS.get(unit, 1)

def less_than(first, second):
    """
    Return true if first is less than second.  These are strings with units ala 100m, 10Mi, etc..
    """
    return as_value(first) < as_value(second)

def greater_than(first, second):
    """
    Return true if first is greater than second.  These are strings with units ala 100m, 10Mi, etc..
    """
    return as_value(first) > as_value(second)

def see_limits():
    """
    View all current namespace limits.

    Usage:
      see_limits [--context=<context>] [--namespace=<namespace>]

    Options:
      --context=<context>   kube context [default: None]
      --namespace=<namespace> kube namespace [default: None]
    """
    args = docopt.docopt(see_limits.__doc__)
    context = args['--context']
    exit_code = 0
    errors = []

    if context == "None":
        LOG.debug('Defaulting to the current kubectl context')
        context = sh.kubectl.config('current-context').stdout.strip().decode('ascii')

    namespace_name = args['--namespace']

    tv = TableView()
    namespace = TableView('Namespace', center=True, link="namespace")
    pod_name = TableView('Pod Name', center=True, link="pod.name")
    container_name = TableView('Container Name', center=True, link="container.name")

    pod = TableView('Pod', center=True)
    pod_cpu = TableView('CPU', center=True)
    pod_cpu_min = TableView('min', center=True, link="pod.min.cpu")
    pod_cpu_max = TableView('max', center=True, link="pod.max.cpu")
    pod_cpu.add_columns([pod_cpu_min, pod_cpu_max])

    pod_mem = TableView('Memory', center=True)
    pod_mem_min = TableView('min', center=True, link="pod.min.memory")
    pod_mem_max = TableView('max', center=True, link="pod.max.memory")
    pod_mem.add_columns([pod_mem_min, pod_mem_max])

    pod.add_columns([pod_cpu, pod_mem])

    container = TableView('Container', center=True)
    container_restart = TableView('Restarts', center=True, link="container.restart")
    container_last = TableView('LastState', center=True, link="container.laststate")

    container_cpu = TableView('CPU', center=True)
    container_cpu_min = TableView('min', center=True, link="container.min.cpu")
    container_cpu_max = TableView('max', center=True, link="container.max.cpu")
    container_cpu.add_columns([container_cpu_min, container_cpu_max])

    container_mem = TableView('Memory', center=True)
    container_mem_min = TableView('min', center=True, link="container.min.memory")
    container_mem_max = TableView('max', center=True, link="container.max.memory")
    container_mem.add_columns([container_mem_min, container_mem_max])

    container_default = TableView('Default', center=True)
    container_default_cpu = TableView('cpu', center=True, link="container.default.cpu")
    container_default_mem = TableView('mem', center=True, link="container.default.memory")
    container_default.add_columns([
        container_default_cpu, container_default_mem
    ])

    container_defaultreq = TableView('Def Request', center=True)
    container_defaultreq_cpu = TableView('cpu', center=True, link="container.defaultRequest.cpu")
    container_defaultreq_mem = TableView('mem', center=True, link="container.defaultRequest.memory")
    container_defaultreq.add_columns([
        container_defaultreq_cpu, container_defaultreq_mem
    ])

    container_maxratio = TableView('Max Ratio', center=True)
    container_maxratio_cpu = TableView('cpu', center=True, link="container.maxLimitRequestRatio.cpu")
    container_maxratio.add_column(container_maxratio_cpu)

    container.add_columns([
        container_restart, container_last, container_cpu, container_mem,
        container_default, container_defaultreq, container_maxratio
    ])

    pvc = TableView('PVC', center=True)
    pvc_min = TableView('min', center=True, link="pvc.min.storage")
    pvc_max = TableView('max', center=True, link="pvc.max.storage")
    pvc.add_columns([pvc_min, pvc_max])

    tv.add_columns([namespace, pod_name, container_name, pod, container, pvc])

    if namespace_name in [None, "None"]:
        # all
        namespaces = []
        kube = kubelib.KubeConfig(context=context, namespace="")
        namespace_objects = kubelib.Namespace(kube).get_list()
        for ns in namespace_objects:
            namespaces.append(ns.metadata.name)
    else:
        namespaces = [namespace_name]

    for namespace_name in namespaces:
        kube = kubelib.KubeConfig(context=context, namespace=namespace_name)
        limits_list = _get_namespace_limits(kube, namespace_name)

        namespace_limit = {
            'namespace': namespace_name,
            'pod.name': '',
            'container.name': ''
        }
        for limits in limits_list:
            # can there be multiple namespace limits?
            for limit in limits.spec.limits:
                if limit.type == "Pod":
                    namespace_limit['pod.min.cpu'] = limit.min.cpu
                    namespace_limit['pod.min.memory'] = limit.min.memory
                    namespace_limit['pod.max.cpu'] = limit.max.cpu
                    namespace_limit['pod.max.memory'] = limit.max.memory

                elif limit.type == "Container":
                    namespace_limit['container.min.cpu'] = limit.min.cpu
                    namespace_limit['container.min.memory'] = limit.min.memory
                    namespace_limit['container.max.cpu'] = limit.max.cpu
                    namespace_limit['container.max.memory'] = limit.max.memory
                    # default
                    namespace_limit['container.default.cpu'] = limit.default.cpu
                    namespace_limit['container.default.memory'] = limit.default.memory
                    # defrequest
                    namespace_limit['container.defaultRequest.cpu'] = limit.defaultRequest.cpu
                    namespace_limit['container.defaultRequest.memory'] = limit.defaultRequest.memory

                    # maxratio
                    namespace_limit['container.maxLimitRequestRatio.cpu'] = limit.maxLimitRequestRatio.cpu

                elif limit.type == "PersistentVolumeClaim":
                    namespace_limit['pvc.min.storage'] = limit.min.storage
                    namespace_limit['pvc.max.storage'] = limit.max.storage

        data = [
            namespace_limit
        ]

        # gather pod limits
        pods = kubelib.Pod(kube).get_list()
        for pod in pods:
            pod_name = pod.metadata.name
            cstatus = {}
            for cstatus_dict in pod.status.containerStatuses:
                cstatus[cstatus_dict['name']] = cstatus_dict

            for container in pod.spec.containers:
                row = {
                    'namespace': namespace_name,
                    'pod.name': pod_name,
                    'container.name': container.name,
                    'container.restart': cstatus[container.name].restartCount,
                    'container.laststate': cstatus[container.name].lastState.get('terminated', {}).get('reason', ''),
                    'container.min.cpu': container.get(
                        "resources", {}
                    ).get(
                        'requests', {}
                    ).get(
                        'cpu',
                        namespace_limit.get('container.defaultRequest.cpu', '')
                    ),
                    'container.min.memory': container.get(
                        "resources", {}
                    ).get(
                        'requests', {}
                    ).get(
                        'memory',
                        namespace_limit.get('container.defaultRequest.memory', '')
                    ),
                    'container.max.cpu': container.get(
                        "resources", {}
                    ).get(
                        'limits', {}
                    ).get(
                        'cpu',
                        namespace_limit.get('container.default.cpu', '')
                    ),
                    'container.max.memory': container.get(
                        "resources", {}
                    ).get(
                        'limits', {}
                    ).get(
                        'memory', namespace_limit.get('container.default.memory', '')
                    ),
                }

                if less_than(row['container.min.memory'], namespace_limit.get('container.min.memory')):
                    msg = ('%s Pod %s min memory %s is below namespace minimum %s' % (
                        namespace_name,
                        pod_name,
                        row['container.min.memory'],
                        namespace_limit.get('container.min.memory')
                    ))
                    print(msg)
                    errors.append(msg)
                    exit_code = 1

                if greater_than(row['container.max.memory'], namespace_limit.get('container.max.memory')):
                    msg = ('%s Pod %s max memory %s is greater than namespace maximum %s' % (
                        namespace_name,
                        pod_name,
                        row['container.max.memory'],
                        namespace_limit.get('container.max.memory')
                    ))
                    print(msg)
                    errors.append(msg)
                    exit_code = 1

                if less_than(row['container.min.cpu'], namespace_limit.get('container.min.cpu')):
                    msg = ('%s Pod %s min cpu %s is below namespace minimum %s' % (
                        namespace_name,
                        pod_name,
                        row['container.min.cpu'],
                        namespace_limit.get('container.min.cpu')
                    ))
                    print(msg)
                    errors.append(msg)
                    exit_code = 1

                if greater_than(row['container.max.cpu'], namespace_limit.get('container.max.cpu')):
                    msg = ('%s Pod %s max cpu %s is greater than namespace maximum %s' % (
                        namespace_name,
                        pod_name,
                        row['container.max.cpu'],
                        namespace_limit.get('container.max.cpu')
                    ))
                    print(msg)
                    errors.append(msg)
                    exit_code = 1

                    # colorama.Fore.RED + "!! " + str + " !!" + colorama.Style.RESET_ALL)
                data.append(row)

        tv.set_data(data)
        print(tv)

    if errors:
        for err in errors:
            print(err)
    sys.exit(exit_code)

if __name__ == "__main__":
    import doctest
    doctest.testmod()
