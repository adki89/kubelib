import bunch
import glob
import json
import os
import requests
import sh
import sys
import time
import yaml

import logging
logger = logging.getLogger(__name__)
logging.info('Starting...')

apiVersion = "v1"

#: Mapping of kubernetes resource types (pvc, pods, service, etc..) to the
#: strings that Kubernetes wants in .yaml file 'Kind' fields.
TYPE_TO_KIND = {
    'pvc': 'PersistentVolumeClaim',
    'services': "Service",
}

# flexible in what we accept, strict in what we provide
CANONICAL_TYPE = {
    'ns': 'namespaces',
    'pod': "pods",
    'pv': 'persistentvolumes',
    'pvc': 'persistentvolumeclaims',
    'rc': 'replicationcontrollers',
    'node': 'nodes'
}


class TimeOut(Exception):
    """maximum timeout exceeded"""


class KubeError(Exception):
    """Generic Kubernetes error wrapper"""


class Kubectl(object):
    """Wrapper around the kubernetes api."""

    def __init__(self, context=None, namespace=None, dryrun=False):
        """Create new Kubectl object.

        :params context: Kubernetes context for this object
        :params namespace: Kubernetes namespace, defaults to the current default
        :params dryrun: When truthy don't actually send anything to kubectl
        """
        with open(os.path.expanduser("~/.kube/config")) as h:
            self.config = bunch.Bunch.fromYAML(
                h.read()
            )

        if context is None:
            # default to the current context
            context = self.config['current-context']
        #: Kubernetes context
        self.context = context

        #if namespace is None:
        # default to the current kubectl namespace
        for c in self.config.contexts:
            if c.name == context:
                if namespace is None:
                    namespace = c.context.get('namespace')

                cluster_name = c.context.cluster
                user_name = c.context.user

        for cluster in self.config.clusters:
            if cluster.name == cluster_name:
                self.cluster = cluster.cluster
                self.cluster.name = cluster_name

        for user in self.config.users:
            if user.name == user_name:
                self.user = user.user
                self.user.name = user_name

        #: Kubernetes namespace
        self.namespace = namespace

        #: Boolean indicating if we don't want to actually send
        #: anything to kubectl
        self.dryrun = dryrun

        self.client = requests.Session()
        self.ca = os.path.expanduser("~/.kube/{}".format(
            self.cluster['certificate-authority']
        ))

        self.cert=(
            os.path.expanduser("~/.kube/{}".format(
                self.user["client-certificate"]
            )),
            os.path.expanduser("~/.kube/{}".format(
                self.user["client-key"]
            ))
        )

        #self.base_resources = self._get_base_resources()

    def _get(self, url, *args, **kwargs):
        url = self.cluster.server + "/api/v1" + url
        if self.dryrun:
            print("GET %r (%r, %r)" % (url, args, kwargs))
            return None
        else:
            response = self.client.get(url, *args, cert=self.cert, verify=self.ca, **kwargs)
            return response.json()

    def _post(self, url, data):
        url = self.cluster.server + "/api/v1" + url
        if self.dryrun:
            print("POST %r (%r)" % (url, data))
            return None
        else:
            response = self.client.post(url, json=data, cert=self.cert, verify=self.ca)
            return response.json()

    def _delete(self, url):
        url = self.cluster.server + "/api/v1" + url
        if self.dryrun:
            print("DELETE %r" % url)
            return None
        else:
            response = self.client.delete(url, cert=self.cert, verify=self.ca)
            return response.json()

    def _get_base_resources(self):
        base_resources = []
        for resource in self._get("/")['resources']:
            base_resources.append(resource['name'])
        return base_resources
    # pods

    def get_pod(self, pod_name_unique):
        """Return an object describing the given pod

        :param pod_name_unique: Full name of the pod
        :returns: Object of pod attributes
        """
        return self.get_resource('pod', pod_name_unique)

    def wait_for_pod(self, pod_name, max_delay=300):
        """Block until the given pod is running.

        Returns the full unique pod name

        :param pod_name: Pod name (without unique suffix)
        :param max_delay: Maximum number of seconds to wait
        :returns: Unique pod name
        :raises TimeOut: When max_delay is exceeded
        """
        start = time.time()

        while 1:
            pods = self.get_resource('pod')
            for pod in pods:
                if pod.metadata.generateName == pod_name + '-':
                    if pod.status.phase == "Running":
                        return pod.metadata.name
                    else:
                        log.info(
                            "Container %s found but status is %s",
                            pod.metadata.name,
                            pod.status.phase
                        )

            # ok, we got through the list of all pods without finding
            # an acceptable running pod.  So we pause (briefly) and try again.

            if time.time() - start > max_delay:
                raise TimeOut('Maximum delay {} exceeded'.format(max_delay))
            else:
                time.sleep(2)

    # namespaces

    def get_namespaces(self):
        """Retrieve namespace objects from kubernetes.

        :returns: List of namespace objects
        """
        return bunch.bunchify(self._get("/namespaces")['items'])

    def create_namespace(self, namespace):
        """Create the given namespace.

        This almost makes the new namespace our new default for
        subsequent operations.

        :param namespace: name of the namespace we want to create
        :returns: True if the create succeeded, False otherwise (it already exists)
        """
        self.namespace = namespace

        response = self._post(
            "/namespaces",
            data={
                "kind": "Namespace",
                "apiVersion": apiVersion,
                "metadata": {
                    "name": namespace,
                }
            }
        )
        if response['status'] == "Failure":
            raise KubeError(response)

    def delete_namespace(self, namespace):
        """Delete the given namespace.

        :param namespace: name of the namespace we want to delete
        """
        if self.namespace == namespace:
            self.namespace = None

        response = self._delete(
            "/namespaces/{name}".format(
                name=namespace
            )
        )
        return response

    # generic

    def get_resource(self, resource_type, single=None):
        """Retrieve one or more resource objects.  To get one
        object you need to provide the object name.

        :param resource_type: simple resource type (service, pod, rc, etc..)
        :param single: particular resource we want [default: None]
        :returns: One resource object or a list of resource objects
        """
        if single is None:
            resources = bunch.bunchify(
                self._get('/namespaces/{namespace}/{resource_type}'.format(
                    namespace=self.namespace,
                    resource_type=CANONICAL_TYPE.get(resource_type, resource_type)
                ))
            )

            return resources["items"]

        else:
            result = self._get('/namespaces/{namespace}/{resource_type}/{name}'.format(
                    namespace=self.namespace,
                    resource_type=CANONICAL_TYPE.get(resource_type, resource_type),
                    name=single
                ))

            return bunch.bunchify(result)

    def create_path(self, path_or_fn):
        """Simple kubectl create wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        self.kubectl.create(
            '-f', path_or_fn,
            '--namespace={}'.format(self.namespace),
            '--context={}'.format(self.context)
        )

    def delete_path(self, path_or_fn):
        """Simple kubectl delete wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        try:
            self.kubectl.delete(
                '-f', path_or_fn,
                '--namespace={}'.format(self.namespace),
                '--context={}'.format(self.context)
            )
        except sh.ErrorReturnCode:
            return False
        return True

    def create_if_missing(self, resource_type, path):
        """Make sure all resources of the given *resource_type* described
        by the yaml file(s) at the given *path* location exist.  Create them
        if they don't.

        :param resource_type: simple resource type (service, pod, rc, etc..)
        :param path: location of resource files
        """
        all_resources = bunch.Bunch.fromYAML(
            self.kubectl.get(
                resource_type,
                '--namespace={}'.format(self.namespace),
                '--context={}'.format(self.context),
                '-o', 'yaml'
            ).stdout
        )

        for resource_fn in glob.glob(path):
            with open(resource_fn) as h:
                resource = yaml.load(h)
            # if TYPE_TO_KIND fails with keyerror you probably need to add a
            # new entry to the dict above.
            if resource['kind'] == TYPE_TO_KIND[resource_type]:

                if resource['metadata']['name'] not in getattr(self, resource_type):
                    logger.info('Creating {}'.format(resource_fn))
                    self.create_path(resource_fn)
                    # placeholder
                    getattr(self, resource_type)[resource['metadata']['name']] = True

    def delete_by_type(self, resource_type):
        """loop through and destroy all resources of the given type.

        :param resource_type: simple resource type (service, pod, rc, etc..)
        """
        for resource in bunch.Bunch.fromYAML(self.kubectl.get(
            resource_type,
            '--context={}'.format(self.context),
            '--namespace={}'.format(self.namespace),
            '-o', 'yaml'
        ).stdout)["items"]:
            resource_name = resource.metadata.name
            logging.info('kubectl delete %s %s', resource_type, resource_name)

            try:
                self.kubectl.delete(
                    resource_type,
                    resource_name,
                    '--context={}'.format(self.context),
                    '--namespace={}'.format(self.namespace)
                )
            except sh.ErrorReturnCode as err:
                logging.error("Unexpected response: %r", err)


    # black magic

    def copy_to_pod(self, source_fn, pod, destination_fn):
        """Copy a file into the given pod.

        This can be handy for dropping files into a pod for testing.

        You need to have passwordless ssh access to the node and
        be a member of the docker group there.

        :param source_fn: path and filename you want to copy
        :param container: pod name you want to copy the file into
        :param destination_fn: path and filename to place it (path must exist)
        """
        pod_obj = self.get_pod(pod)
        tempfn = "temp.fn"
        node_name = pod_obj.spec.nodeName

        sh.scp(source_fn, "{node_name}:{tempfn}".format(
            node_name=node_name,
            tempfn=tempfn
        ))

        containerID = pod.status["containerStatuses"][0]["containerID"][9:21]

        command = "docker cp {origin} {container}:{destination}".format(
            origin=tempfn,
            container=containerID,
            destination=destination_fn
        )

        sh.ssh(node, command)

    # volumes

    def clean_volumes(self):
        """Delete and rebuild any persistent volume in a released
        or failed state.
        """
        for pv in self.get_resource('pv'):
            if pv.status.phase in ['Released', 'Failed']:
                logger.info('Rebuilding PV %s', pv.metadata['name'])
                self.kubectl.delete.pv(pv.metadata.name, context=self.context)
                self.kubectl.create(context=self.context, _in=pv.toYAML())


def maybeint(maybe):
    """
    If it's an int, make it an int.  otherwise leave it as a string.

    >>> maybeint("12")
    12

    >>> maybeint("cow")
    'cow'
    """
    try:
        maybe = int(maybe)
    except ValueError:
        pass
    return maybe

def reimage(filename, xpath, newvalue, save_to=None):
    """
    Replace the given location (xpath) in the yaml filename with the value
    newvalue.

    >>> reimage("./tests/reimage_test.yml", "alpha.beta.gamma.0.delta", "epsilon", "./tests/reimage_test_done.yml")
    {'alpha': {'beta': {'gamma': [{'a': 'silly', 'c': 'are', 'b': 'tests', 'e': 'useful', 'd': 'often', 'f': 'working', 'delta': 'epsilon'}, {'a': 'dummy'}]}}, 'junk': {'this': {'is': [{'just': 'noise'}]}}}
    """
    with open(filename, 'r') as h:
        yml = yaml.load(h.read())

    sub_yml = yml
    xplst = xpath.split('.')
    for pcomp in xplst[:-1]:
        pcomp = maybeint(pcomp)
        sub_yml = sub_yml[pcomp]

    sub_yml[xplst[-1]] = newvalue

    if save_to is None:
        save_to = filename

    with open(save_to, 'w') as h:
        h.write(yaml.dump(yml))
    return yml
