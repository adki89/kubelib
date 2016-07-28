"""
Library of Kubernetes flavored helpers and objects
"""

import logging
import os
import time
import yaml

import bunch
import glob2
import requests
import sh

LOG = logging.getLogger(__name__)
logging.info('Starting...')


class TimeOut(Exception):
    """maximum timeout exceeded"""

class KubeError(Exception):
    """Generic Kubernetes error wrapper"""

class ContextRequired(KubeError):
    """Everything requires a context"""

class ClusterNotFound(KubeError):
    """Probably a bad ~/.kube/config"""

class KubeConfig(object):
    config = None
    context = None
    namespace = None
    cluster = None
    context_obj = None

    def __init__(self, context=None, namespace=None):
        """Create new KubeConfig object.

        :params context: Kubernetes context for this object
        :params namespace: Kubernetes namespace, defaults to the current default
        """
        with open(os.path.expanduser("~/.kube/config")) as handle:
            self.config = bunch.Bunch.fromYAML(
                handle.read()
            )

        self.set_context(context)

        #if namespace is None:
        # default to the current kubectl namespace
        cluster_name = None
        for context_obj in self.config.contexts:
            if context_obj.name == self.context:
                self.context_obj = context_obj

        if self.context_obj is None:
            raise ContextRequired('Context %r not found' % self.context)

        self.set_namespace(namespace)

        cluster_name = self.context_obj.context.cluster
        user_name = self.context_obj.context.user

        if cluster_name is None:
            raise ClusterNotFound('Context %r has no cluster' % context)

        for cluster in self.config.clusters:
            if cluster.name == cluster_name:
                self.cluster = cluster.cluster
                self.cluster.name = cluster_name

        if self.cluster is None:
            raise ClusterNotFound('Failed to find cluster: %r' % cluster_name)

        for user in self.config.users:
            if user.name == user_name:
                self.user = user.user
                self.user.name = user_name

        self.ca = os.path.expanduser("~/.kube/{}".format(
            self.cluster['certificate-authority']
        ))

        self.cert = (
            os.path.expanduser("~/.kube/{}".format(
                self.user["client-certificate"]
            )),
            os.path.expanduser("~/.kube/{}".format(
                self.user["client-key"]
            ))
        )

    def set_context(self, context=None):
        if context is None:
            # default to the current context
            context = self.config['current-context']
        #: Kubernetes context
        self.context = context

    def set_namespace(self, namespace=None):
        if namespace is None:
            namespace = self.context_obj.context.get('namespace')

        self.namespace = namespace

class KubeUtils(KubeConfig):

    def apply_path(self, path, recursive=False):

        if recursive:
            path += "/**/*"
        else:
            path += "/*"

        cache = {}
        for resource_fn in glob2.glob(path):
            LOG.info('Applying %r', resource_fn)
            if os.path.isdir(resource_fn):
                continue

            if not resource_fn.endswith(('.yml', '.yaml')):
                continue

            with open(resource_fn, 'r') as handle:
                resource_content = handle.read()
                resource_desc = bunch.Bunch.fromYAML(
                    yaml.load(resource_content)
                )

            if resource_desc.kind not in cache:
                resource_class = resource_by_kind(resource_desc.kind)
                resource = resource_class(self)
                cache[resource_desc.kind] = resource

            cache[resource_desc.kind].apply(resource_desc, resource_fn)

    def copy_to_pod(self, source_fn, pod, destination_fn):
        """Copy a file into the given pod.

        This can be handy for dropping files into a pod for testing.

        You need to have passwordless ssh access to the node and
        be a member of the docker group there.

        :param source_fn: path and filename you want to copy
        :param container: pod name you want to copy the file into
        :param destination_fn: path and filename to place it (path must exist)
        """
        success = False
        max_retry = 20
        retry_count = 0

        while success == False and retry_count < max_retry:
            pod_obj = Pod(self).get(pod)
            tempfn = "temp.fn"
            try:
                node_name = pod_obj.spec.nodeName
                success = True
            except AttributeError as err:
                LOG.error('Error collecting node data: %r', err)
                LOG.error('pod_obj: %r', pod_obj)
                time.sleep(2)
                retry_count += 1

        destination = "{node_name}:{tempfn}".format(
            node_name=node_name,
            tempfn=tempfn
        )
        LOG.info('scp %r %r', source_fn, destination)
        sh.scp(source_fn, destination)

        container_id = pod_obj.status["containerStatuses"][0]["containerID"][9:21]

        command = "docker cp {origin} {container}:{destination}".format(
            origin=tempfn,
            container=container_id,
            destination=destination_fn
        )

        sh.ssh(node_name, command)

    def delete_by_type(self, resource_type):
        """loop through and destroy all resources of the given type.

        :param resource_type: resource type (Service, Pod, etc..)
        """
        resource = resource_by_kind(resource_type)(self)

        for resource_obj in resource.get_list():
            resource.delete(resource_obj.metadata.name)

    def clean_volumes(self):
        """Delete and rebuild any persistent volume in a released
        or failed state.
        """
        for pv in PersistentVolume(self).get_list():
            if pv.status.phase in ['Released', 'Failed']:
                LOG.info('Rebuilding PV %s', pv.metadata['name'])
                self.kubectl.delete.pv(pv.metadata.name, context=self.context)
                self.kubectl.create(
                    "--save-config",
                    context=self.context,
                    _in=pv.toYAML(),
                )

class Kubernetes(object):
    api_base = "/api/v1"

    def __init__(self, kubeconfig):
        self.config = kubeconfig
        self.client = requests.Session()
        self.client.cert = kubeconfig.cert
        self.client.verify = self.config.ca
        self.kubectl = sh.kubectl

    def _get(self, url, **kwargs):
        url = self.config.cluster.server + self.api_base + url
        response = self.client.get(url, **kwargs)
        return response.json()

    def _post(self, url, data):
        url = self.config.cluster.server + self.api_base + url
        response = self.client.post(url, json=data)
        return response.json()

    def _delete(self, url):
        url = self.config.cluster.server + self.api_base + url
        response = self.client.delete(url)
        return response.json()

class ResourceBase(Kubernetes):
    url_type = None
    list_uri = "/namespaces/{namespace}/{resource_type}"
    single_uri = "/namespaces/{namespace}/{resource_type}/{name}"

    def get_list(self):
        url = self.list_uri.format(
            namespace=self.config.namespace,
            resource_type=self.url_type
        )
        resources = bunch.bunchify(
            self._get(url)
        )
        LOG.debug('get_list resources: %r', resources)
        return resources.get("items", [])

    def get(self, name):
        return bunch.bunchify(
            self._get(self.api_base, self.single_uri.format(
                namespace=self.config.namespace,
                resource_type=self.url_type,
                name=name
            ))
        )

    def delete(self, name):
        """delete the named resource

        TODO: should be able to rewrite this as a kube api
        delete call instead of going through kubectl.
        """

        try:
            self.kubectl.delete(
                self.url_type,
                name,
                '--context={}'.format(self.config.context),
                '--namespace={}'.format(self.config.namespace)
            )
        except sh.ErrorReturnCode as err:
            logging.error("Unexpected response: %r", err)

class ActorBase(ResourceBase):
    aliases = []
    cache = None

    def replace_path(self, path_or_fn):
        """Simple kubectl replace wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        self.kubectl.replace(
            '-f', path_or_fn,
            '--namespace={}'.format(self.config.namespace),
            '--context={}'.format(self.config.context)
        )

    def create_path(self, path_or_fn):
        """Simple kubectl create wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        self.kubectl.create(
            '-f', path_or_fn,
            '--namespace={}'.format(self.config.namespace),
            '--context={}'.format(self.config.context),
            '--save-config'
        )

    def delete_path(self, path_or_fn):
        """Simple kubectl delete wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        try:
            self.kubectl.delete(
                '-f', path_or_fn,
                '--namespace={}'.format(self.config.namespace),
                '--context={}'.format(self.config.context)
            )
        except sh.ErrorReturnCode:
            return False
        return True

    def apply(self, desc, filename):
        return

    def exists(self, name, force_reload=False):
        """Does resource 'name' exist in kubes?"""
        if self.cache is None or force_reload:
            self.cache = {}
            res_list = self.get_list()
            for res in res_list:
                self.cache[res.metadata.name] = res

        return name in self.cache

class DeleteCreateActor(ActorBase):
    """Delete this resource and re-create it"""
    def apply(self, desc, filename):
        if self.exists(desc.metadata.name):
            self.delete_path(filename)
        self.create_path(filename)

class ReplaceActor(ActorBase):
    """Do a kubectl replace on this object"""
    def apply(self, desc, filename):
        if self.exists(desc.metadata.name):
            self.replace_path(filename)
        else:
            self.create_path(filename)

class CreateIfMissingActor(ActorBase):
    """Create only if missing"""
    def apply(self, desc, filename):
        if not self.exists(desc.metadata.name):
            self.create_path(filename)

class IgnoreActor(ActorBase):
    """null-op, don't do anything"""

########################################

class Deployment(ReplaceActor):
    url_type = 'deployments'
    api_base = "/apis/extensions/v1beta1"

class DaemonSet(ReplaceActor):
    url_type = "daemonsets"
    api_base = "/apis/extensions/v1beta1"

class Namespace(CreateIfMissingActor):
    url_type = "namespaces"
    aliases = ['ns']
    list_uri = "/{resource_type}"
    single_uri = "/{resource_type}/{name}"

    def create(self, namespace):
        """Create the given namespace.

        :param namespace: name of the namespace we want to create
        :returns: True if the create succeeded, False otherwise (it already exists)
        """
        response = self._post(
            "/namespaces",
            data={
                "kind": "Namespace",
                "apiVersion": "v1",
                "metadata": {
                    "name": namespace,
                }
            }
        )
        if response['status'] == "Failure":
            # I would rather raise.. but want to stay backward
            # compatible for a little while.
            # raise KubeError(response)
            return False

        return True

    def delete(self, namespace):
        """Delete the given namespace.

        :param namespace: name of the namespace we want to delete
        """
        response = self._delete(
            "/namespaces/{name}".format(
                name=namespace
            )
        )
        return response

class Node(IgnoreActor):
    url_type = "nodes"
    aliases = ['node']

class PersistentVolume(CreateIfMissingActor):
    url_type = "persistentvolumes"
    aliases = ['pv']
    list_uri = "/{resource_type}"
    single_uri = "/{resource_type}/{name}"

class PersistentVolumeClaim(CreateIfMissingActor):
    url_type = "persistentvolumeclaims"
    aliases = ['pvc']

class Pod(IgnoreActor):
    url_type = "pods"
    aliases = ['pod']

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
            pods = self.get_list()
            for pod in pods:
                if pod.metadata.generateName == pod_name + '-':
                    if pod.status.phase == "Running":
                        return pod.metadata.name
                    else:
                        LOG.info(
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

    def exec_cmd(self, pod, container, *command):
        """Execute a command in a pod/container.  If container is None
        the kubectl behavior is to pick the first container if possible
        """
        if container is None:
            return self.kubectl(
                'exec',
                pod,
                '--namespace={}'.format(self.config.namespace),
                '--context={}'.format(self.config.context),
                '--', *command
            )
        else:
            return self.kubectl(
                'exec',
                pod,
                '--namespace={}'.format(self.config.namespace),
                '--context={}'.format(self.config.context),
                '-c', container,
                '--', *command
            )

class Policy(CreateIfMissingActor):
    url_type = "policy"
    api_base = "abac.authorization.kubernetes.io/v1beta1"

class ReplicationController(DeleteCreateActor):
    url_type = "replicationcontrollers"

class Role(CreateIfMissingActor):
    url_type = "role"
    api_base = "rbac.authorization.k8s.io/v1alpha1"

class ClusterRole(CreateIfMissingActor):
    url_type = "clusterrole"
    api_base = "rbac.authorization.k8s.io/v1alpha1"

class RoleBinding(CreateIfMissingActor):
    url_type = "rolebinding"
    api_base = "rbac.authorization.k8s.io/v1alpha1"

class ClusterRoleBinding(CreateIfMissingActor):
    url_type = "clusterrolebinding"
    api_base = "rbac.authorization.k8s.io/v1alpha1"

class Service(CreateIfMissingActor):
    url_type = "services"

class Secret(CreateIfMissingActor):
    url_type = "secret"

    def create(self, name, dict_of_secrets):
        encoded_dict = {}
        for key in dict_of_secrets:
            encoded_dict[key] = base64.b64encode(dict_of_secrets[key])

        response = self._post(
            "/namespaces/{namespace}/secrets".format(
                namespace=self.config.namespace
            ),
            data={
                "kind": "Secret",
                "apiVersion": "v1",
                "metadata": {
                    "name": name,
                },
                "type": "Opaque",
                "data": encoded_dict
            }
        )
        if response['status'] == "Failure":
            raise KubeError(response)

########################################

def resource_by_kind(kind):
    for resource in RESOURCE_CLASSES:
        if resource.__name__ == kind:
            return resource

# simple convenience wrappers for KubeUtils functions

def apply_path(
    path,
    context=None,
    namespace=None,
    recursive=True
):
    config = KubeUtils(context=context, namespace=namespace)

    config.apply_path(
        path, recursive
    )

def delete_by_type(resource_type, context, namespace):
    """loop through and destroy all resources of the given type.

    :param resource_type: resource type (Service, Pod, etc..)
    """
    config = KubeUtils(context=context, namespace=namespace)
    config.delete_by_type(resource_type)

def copy_to_pod(source_fn, pod, destination_fn, context, namespace):
    config = KubeUtils(context=context, namespace=namespace)
    return config.copy_to_pod(source_fn, pod, destination_fn)

def _maybeint(maybe):
    """
    If it is an int, make it an int.  otherwise leave it as a string.

    >>> _maybeint("12")
    12

    >>> _maybeint("cow")
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
    with open(filename, 'r') as handle:
        yml = yaml.load(handle.read())

    sub_yml = yml
    xplst = xpath.split('.')
    for pcomp in xplst[:-1]:
        pcomp = _maybeint(pcomp)
        sub_yml = sub_yml[pcomp]

    sub_yml[xplst[-1]] = newvalue

    if save_to is None:
        save_to = filename

    with open(save_to, 'w') as handle:
        handle.write(yaml.dump(yml))
    return yml

RESOURCE_CLASSES = (
    Deployment, DaemonSet, Namespace,
    PersistentVolume, PersistentVolumeClaim,
    ReplicationController, Service
)

TYPE_TO_KIND = {}
for resource in RESOURCE_CLASSES:
    TYPE_TO_KIND[resource.url_type] = resource.__name__

# inverse
KIND_TO_TYPE = {}
for kube_type in TYPE_TO_KIND:
    KIND_TO_TYPE[TYPE_TO_KIND[kube_type]] = kube_type

# backward compatibility

class Kubectl(KubeUtils):
    def __init__(self, context=None, namespace=None, dryrun=None):
        super(Kubectl, self).__init__(context, namespace)

    def create_namespace(self, namespace):
        return Namespace(self).create(namespace)

    def create_path(self, path_or_fn):
        self.kubectl.create(
            '--save-config',
            "-f {}".format(path_or_fn),
            context=self.config.context,
            namespace=self.config.namespace
        )

    def delete_path(self, path_or_fn):
        self.kubectl.delete(
            "-f {}".format(path_or_fn),
            context=self.config.context,
            namespace=self.config.namespace
        )

    def create_if_missing(self, friendly_resource_type, glob_path):

        cache = {}
        for resource_fn in glob2.glob(glob_path):
            with open(resource_fn) as handle:
                resource_desc = bunch.Bunch.fromYAML(
                    yaml.load(handle.read())
                )

            # is this a friendly_resource_type?
            friendly_to_kind = {
                'componentstatuses': 'ComponentStatus',
                'cs': 'ComponentStatus',
                'configmaps': 'ConfigMap',
                'daemonsets': '',
                'ds': '',
                'deployments': '',
                'events': 'Event',
                'ev': 'Event',
                'endpoints': '',
                'ep': '',
                'horizontalpodautoscalers': '',
                'hpa': '',
                'ingress': '',
                'ing': '',
                'jobs': '',
                'limitranges': '',
                'limits': '',
                'nodes': '',
                'no': '',
                'namespaces': '',
                'ns': '',
                'pods': 'Pod',
                'po': 'Pod',
                'persistentvolumes': 'PersistentVolume',
                'pv': 'PersistentVolume',
                'persistentvolumeclaims': 'PersistentVolumeClaim',
                'pvc': 'PersistentVolumeClaim',
                'quota': '',
                'resourcequotas': '',
                'replicasets': '',
                'rs': '',
                'replicationcontrollers': '',
                'rc': '',
                'secrets': '',
                'serviceaccounts': '',
                'sa': '',
                'services': 'Service',
                'svc': 'Service',
            }
            if resource_desc.kind != friendly_to_kind[friendly_resource_type]:
                continue

            if resource_desc.kind not in cache:
                resource_class = resource_by_kind(resource_desc.kind)
                resource = resource_class(self)
                cache[resource_desc.kind] = resource

            if not cache[resource_desc.kind].exists(desc.metadata.name):
                self.create_path(filename)

