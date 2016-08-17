"""
Library of Kubernetes flavored helpers and objects
"""

import base64
import json
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

    def __init__(self, context=None, namespace=None):
        """Create a new KubeConfig object.  This holds all
        the config information needed to communicate with
        a particular kubernetes cluster.

        :param context: Kubernetes context
        :param namespace: Kubernetes namespace
        """
        self.config = None
        with open(os.path.expanduser("~/.kube/config")) as handle:
            self.config = bunch.Bunch.fromYAML(
                handle.read()
            )

        self.context = None
        self.set_context(context)

        #if namespace is None:
        # default to the current kubectl namespace
        cluster_name = None

        self.context_obj = None
        for context_obj in self.config.contexts:
            if context_obj.name == self.context:
                self.context_obj = context_obj

        if self.context_obj is None:
            raise ContextRequired('Context %r not found' % self.context)

        self.namespace = None
        self.set_namespace(namespace)

        cluster_name = self.context_obj.context.cluster
        user_name = self.context_obj.context.user

        if cluster_name is None:
            raise ClusterNotFound('Context %r has no cluster' % context)

        self.cluster = None
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

        self.vault_client = None

    def set_context(self, context=None):
        """Set the current context.  This is generally
        done during initialization.

        Passing *None* will detect and utilize the current
        default kubectl context.

        :param context: Context to use
        """
        if context is None:
            # default to the current context
            context = self.config['current-context']
        #: Kubernetes context
        self.context = context

    def set_namespace(self, namespace=None):
        """Set the current namespace.  This is generally
        done during initialization.

        Passing *None* will detect and utilize the current
        default kubectl namespace for this context.

        :param namespace: Namespace to use
        """
        if namespace is None:
            namespace = self.context_obj.context.get('namespace')

        self.namespace = namespace

    def set_vault(self, vault_client):
        """helper to assign a pre-autheticated vault client

        :param vault_client: hvac client
        """
        self.vault_client = vault_client


class KubeUtils(KubeConfig):
    """Child of KubeConfig with some helper functions attached"""

    def apply_path(self, path, recursive=False):
        """Apply all the yml or yaml resource files in the given
        directory to the current context/namespace.  Exactly what
        apply means depends on the resource type.

        :param path: Directory of yaml resources
        :param recursive: True to recurse into subdirectories
        """
        path.rstrip('/')

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
                    resource_content
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

        TODO: add container parameter

        :param source_fn: path and filename you want to copy
        :param pod: Pod name
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
        or failed state.  Note: this Persistent Volumes are *not*
        contained by namespace so this is a whole-context operation.
        """
        for pv in PersistentVolume(self).get_list():
            if pv.status.phase in ['Released', 'Failed']:
                LOG.info('Rebuilding PV %s', pv.metadata['name'])
                sh.kubectl.delete.pv(
                    pv.metadata.name,
                    context=self.context
                )
                sh.kubectl.create(
                    "--save-config",
                    context=self.context,
                    _in=pv.toYAML(),
                )

class Kubernetes(object):
    """Base class for communicating with Kubernetes.  Provides
    both HTTP and a kubectl command line wrapper.
    """
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

    def _put(self, url, data):
        url = self.config.cluster.server + self.api_base + url
        response = self.client.put(url, json=data)
        return response.json()

    def _delete(self, url):
        url = self.config.cluster.server + self.api_base + url
        response = self.client.delete(url)
        return response.json()


class ResourceBase(Kubernetes):
    """Base class for particular kinds of kubernetes resources.
    """
    url_type = None
    list_uri = "/namespaces/{namespace}/{resource_type}"
    single_uri = "/namespaces/{namespace}/{resource_type}/{name}"

    def get_list(self):
        """Retrieve a list of bunch objects describing all
        the resources of this type in this namespace/context
        """
        url = self.list_uri.format(
            namespace=self.config.namespace,
            resource_type=self.url_type
        )
        resources = bunch.bunchify(
            self._get(url)
        )
        # LOG.debug('get_list resources: %r', resources)
        return resources.get("items", [])

    def get(self, name):
        """Retrieve a single bunch object describing one
        resource by name
        """
        return bunch.bunchify(
            self._get(self.api_base, self.single_uri.format(
                namespace=self.config.namespace,
                resource_type=self.url_type,
                name=name
            ))
        )

    def delete(self, name):
        """delete the named resource

        TODO: should be easy to rewrite this as a kube api
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
    """Base class for the Actors (the things that actually *do* stuff).
    Different kubernetes resource types need to be manage a bit
    differently.  The Actor sub-classes contain those differences.
    """
    aliases = []
    cache = None
    secrets = False

    def replace_path(self, path_or_fn):
        """Simple kubectl replace wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        LOG.info('(=) kubectl replace -f %s', path_or_fn)
        self.kubectl.replace(
            '-f', path_or_fn,
            '--namespace={}'.format(self.config.namespace),
            '--context={}'.format(self.config.context)
        )

    def create_path(self, path_or_fn):
        """Simple kubectl create wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        LOG.info('(+) kubectl create -f %s', path_or_fn)
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
        LOG.info('(-) kubectl delete -f %s', path_or_fn)
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
        """NOOP Placeholder to be overridden by Actor
        sub-classes.

        :param desc: Bunch resource object
        :param filename: Filename associated with the resource
        """
        return

    def exists(self, name, force_reload=False):
        """returns True if *name* exist in kubes

        :param name: Name of the resource we are interest in
        :param force_reload: Force a fresh cope of inventory (bypass cache)
        """
        LOG.info('(?) does %r:%r exist?', self.url_type, name)
        if self.cache is None or force_reload:
            self.cache = {}
            res_list = self.get_list()
            for res in res_list:
                self.cache[res.metadata.name] = res

        return name in self.cache

    def get_secrets(self, pod_name):
        if self.config.vault_client is None:
            LOG.info('No Vault Client provided, skipping...')
            return

        secret_url = "/secret/{context}/{namespace}/{pod}".format(
                context=self.config.context,
                namespace=self.config.namespace,
                pod=pod_name
            )
        LOG.info('Reading secrets for %r', secret_url)
        try:
            pod_secrets = self.config.vault_client.read(secret_url)['data']
        except Exception as err:
            LOG.error(err)
            LOG.info('Skipping...')

        return pod_secrets

    def simple_name(self, desc):
        return desc.metadata.name

    def apply_secrets(self, desc, filename):
        """If this resource type support secrets and a vault client has
        been assigned we want to check vault to see if there are any
        secrets for this resource.  If there are we want to extract
        the secrets from vault and create them as kubernetes secrets.
        We're also going to patch into the resource yaml to make the
        secrets available as environmentals.

        TODO: support 'type's other than 'env' to automate putting
        files into the container.
        """

        if self.secrets and self.config.vault_client:
            pod_name = self.simple_name(desc)

            pod_secrets = self.get_secrets(pod_name)
            secret_name = pod_name + '-vault'

            # reformulate secrets to a dict suitable for
            # creating kube secrets and a list for injecting
            # into the .yaml
            secrets = {}
            env = []
            LOG.info(pod_secrets)
            for secret in pod_secrets:
                #LOG.info('pod_secrets[%r]: %r', secret, pod_secrets[secret])

                my_secret = json.loads(pod_secrets[secret])
                secrets[secret] = my_secret['value']
                if my_secret.get('type', '') == 'env':
                    val = {
                        "name": my_secret['key'],
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": "secret",
                                "key": secret
                            }
                        }
                    }
                    env.append(val)
                    envdict[my_secret['key']] = val

            if secrets:
                if Secret(self.config).exists(secret_name):
                    LOG.info('Secret %r already exists.  Replacing it.', secret_name)
                    Secret(self.config).replace(secret_name, secrets)
                else:
                    LOG.info('Secret %r does not exist.  Creating it.', secret_name)
                    Secret(self.config).create(secret_name, secrets)

            # new secrets override old ones
            for v in desc.spec.template.spec.containers[0].env:
                if v in envdict:
                    pass
                else:
                    env.append(v)

            reimage(
                filename=filename,
                xpath="spec.template.spec.containers.0.env",
                newvalue=env
            )

            with open(filename, "r") as handle:
                content = handle.read()

            LOG.info('Creating:\n%s' % content)


class DeleteCreateActor(ActorBase):
    """Delete the resource and re-create it"""
    def apply(self, desc, filename):
        self.apply_secrets(desc, filename)

        if self.exists(desc.metadata.name):
            self.delete_path(filename)
        self.create_path(filename)

class ReplaceActor(ActorBase):
    """Do a *kubectl replace* on this object"""
    def apply(self, desc, filename):
        self.apply_secrets(desc, filename)

        if self.exists(desc.metadata.name):
            self.replace_path(filename)
        else:
            self.create_path(filename)

class CreateIfMissingActor(ActorBase):
    """Create only if the resource is missing"""
    def apply(self, desc, filename):
        self.apply_secrets(desc, filename)

        if not self.exists(desc.metadata.name):
            self.create_path(filename)

class IgnoreActor(ActorBase):
    """null-op, don't do anything"""

########################################

class ConfigMap(ReplaceActor):
    """The ConfigMap API resource holds key-value pairs of configuration
    data that can be consumed in pods or used to store configuration data
    for system components such as controllers. ConfigMap is similar to
    Secrets, but designed to more conveniently support working with
    strings that do not contain sensitive information."""
    url_type = 'configmaps'

class Deployment(ReplaceActor):
    """A Deployment provides declarative updates for Pods and
    Replica Sets (the next-generation Replication Controller).
    You only need to describe the desired state in a Deployment
    object, and the Deployment controller will change the actual
    state to the desired state at a controlled rate for you. You
    can define Deployments to create new resources, or replace
    existing ones by new ones."""
    url_type = 'deployments'
    api_base = "/apis/extensions/v1beta1"
    secrets = True

class DaemonSet(ReplaceActor):
    """A Daemon Set ensures that all (or some) nodes run a copy of a
    pod. As nodes are added to the cluster, pods are added to them. As
    nodes are removed from the cluster, those pods are garbage collected.
    Deleting a Daemon Set will clean up the pods it created."""
    url_type = "daemonsets"
    api_base = "/apis/extensions/v1beta1"

class Job(CreateIfMissingActor):
    url_type = "jobs"
    api_base = "/apis/extensions/v1beta1"

class Namespace(CreateIfMissingActor):
    """Kubernetes supports multiple virtual clusters backed by the same
    physical cluster. These virtual clusters are called namespaces."""
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

        self.config.set_namespace(namespace)
        sa = ServiceAccount(self.config)
        if not sa.exists("default"):
            # this will (but not always) fail
            try:
                sa.create("default")
            except sh.ErrorReturnCode_1 as err:
                LOG.error(err)

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
    """Node is a worker machine in Kubernetes, previously known as Minion.
    Node may be a VM or physical machine, depending on the cluster. Each
    node has the services necessary to run Pods and is managed by the
    master components. The services on a node include docker, kubelet
    and network proxy. See The Kubernetes Node section in the architecture
    design doc for more details."""

    url_type = "nodes"
    aliases = ['node']

class PersistentVolume(CreateIfMissingActor):
    """A PersistentVolume (PV) is a piece of networked storage in the cluster
    that has been provisioned by an administrator. It is a resource in the
    cluster just like a node is a cluster resource. PVs are volume plugins
    like Volumes, but have a lifecycle independent of any individual pod that
    uses the PV. This API object captures the details of the implementation of
    the storage, be that NFS, iSCSI, or a cloud-provider-specific storage
    system."""
    url_type = "persistentvolumes"
    aliases = ['pv']
    list_uri = "/{resource_type}"
    single_uri = "/{resource_type}/{name}"

class PersistentVolumeClaim(CreateIfMissingActor):
    """A PersistentVolumeClaim (PVC) is a request for storage by a user. It
    is similar to a pod. Pods consume node resources and PVCs consume PV
    resources. Pods can request specific levels of resources (CPU and Memory).
    Claims can request specific size and access modes (e.g, can be mounted once
    read/write or many times read-only)."""
    url_type = "persistentvolumeclaims"
    aliases = ['pvc']

class Pod(IgnoreActor):
    """A pod (as in a pod of whales or pea pod) is a group of one or more
    containers (such as Docker containers), the shared storage for those
    containers, and options about how to run the containers. Pods are always
    co-located and co-scheduled, and run in a shared context. A pod models an
    application-specific "logical host" - it contains one or more application
    containers which are relatively tightly coupled - in a pre-container world,
    they would have executed on the same physical or virtual machine."""
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

# class Policy(CreateIfMissingActor):
#     url_type = "policy"
#     api_base = "abac.authorization.kubernetes.io/v1beta1"

class ReplicationController(DeleteCreateActor):
    """A replication controller ensures that a specified number of pod "replicas" are
    running at any one time. In other words, a replication controller makes sure that
    a pod or homogeneous set of pods are always up and available. If there are too many
    pods, it will kill some. If there are too few, the replication controller will start
    more. Unlike manually created pods, the pods maintained by a replication controller
    are automatically replaced if they fail, get deleted, or are terminated. For example,
    your pods get re-created on a node after disruptive maintenance such as a kernel
    upgrade. For this reason, we recommend that you use a replication controller even if
    your application requires only a single pod. You can think of a replication controller
    as something similar to a process supervisor, but rather than individual processes on
    a single node, the replication controller supervises multiple pods across multiple
    nodes."""
    url_type = "replicationcontrollers"
    secrets = True

    def simple_name(self, desc):
        return desc.metadata.generateName.split('-')[0]

class Role(CreateIfMissingActor):
    """roles hold a logical grouping of permissions. These permissions map very closely to
    ABAC policies, but only contain information about requests being made. Permission are
    purely additive, rules may only omit permissions they do not wish to grant."""
    url_type = "roles"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"

class ServiceAccount(CreateIfMissingActor):
    url_type = "serviceaccounts"

    def create(self, name="default"):
        return self.kubectl.create.serviceaccount(name, namespace=self.config.namespace)

class ClusterRole(CreateIfMissingActor):
    """ClusterRoles hold the same information as a Role but can apply to any namespace as
    well as non-namespaced resources (such as Nodes, PersistentVolume, etc.)"""
    url_type = "clusterroles"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"

class RoleBinding(CreateIfMissingActor):
    """RoleBindings perform the task of granting the permission to a user or set of users.
    They hold a list of subjects which they apply to, and a reference to the Role being
    assigned.

    RoleBindings may also refer to a ClusterRole. However, a RoleBinding that refers to a
    ClusterRole only applies in the RoleBinding's namespace, not at the cluster level. This
    allows admins to define a set of common roles for the entire cluster, then reuse them
    in multiple namespaces.
    """
    url_type = "rolebindings"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"

class ClusterRoleBinding(CreateIfMissingActor):
    """A ClusterRoleBinding may be used to grant permissions in all namespaces."""
    url_type = "clusterrolebindings"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"

class Service(CreateIfMissingActor):
    """Kubernetes Pods are mortal. They are born and they die, and they
    are not resurrected. ReplicationControllers in particular create and
    destroy Pods dynamically (e.g. when scaling up or down or when doing
    rolling updates). While each Pod gets its own IP address, even those
    IP addresses cannot be relied upon to be stable over time. This leads
    to a problem: if some set of Pods (let's call them backends) provides
    functionality to other Pods (let's call them frontends) inside the
    Kubernetes cluster, how do those frontends find out and keep track of
    which backends are in that set?"""
    url_type = "services"

class Secret(CreateIfMissingActor):
    """Objects of type secret are intended to hold sensitive information,
    such as passwords, OAuth tokens, and ssh keys. Putting this information
    in a secret is safer and more flexible than putting it verbatim in a
    pod definition or in a docker image. See Secrets design document for
    more information."""
    url_type = "secrets"

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

        try:
            if response['kind'] != "Secret":
                raise KubeError(response)
        except KeyError:
            raise KubeError('Unexpected response: %r', response)

    def replace(self, name, dict_of_secrets):
        encoded_dict = {}
        for key in dict_of_secrets:
            encoded_dict[key] = base64.b64encode(dict_of_secrets[key])

        response = self._put(
            "/namespaces/{namespace}/secrets/{name}".format(
                namespace=self.config.namespace,
                name=name
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

        # response is a copy of the secret object
        try:
            if response['kind'] != "Secret":
                raise KubeError(response)
        except KeyError:
            raise KubeError('Unexpected response: %r', response)

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
    """Apply all the yml or yaml resource files in the given
    directory to the given context/namespace.  Exactly what
    apply means depends on the resource type.

    :param context: Kubernetes context
    :param namespace: Kubernetes namespace
    :param path: Directory of yaml resources
    :param recursive: True to recurse into subdirectories
    """
    config = KubeUtils(context=context, namespace=namespace)

    config.apply_path(
        path, recursive
    )

def delete_by_type(resource_type, context, namespace):
    """loop through and destroy all resources of the given type.

    :param resource_type: resource type (Service, Pod, etc..)
    :param context: Kubernetes context
    :param namespace: Kubernetes namespace
    """
    config = KubeUtils(context=context, namespace=namespace)
    config.delete_by_type(resource_type)

def copy_to_pod(source_fn, pod, destination_fn, context, namespace):
    """Copy a file into the given pod.

    This can be handy for dropping files into a pod for testing.

    You need to have passwordless ssh access to the node and
    be a member of the docker group there.

    TODO: add container parameter

    :param source_fn: path and filename you want to copy
    :param pod: Pod name
    :param destination_fn: path and filename to place it (path must exist)

    """
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
    ConfigMap,
    Deployment, DaemonSet, Job, Namespace, Node,
    PersistentVolume, PersistentVolumeClaim,
    Pod, ReplicationController,
    Role, ClusterRole, RoleBinding,
    ClusterRoleBinding, Service, Secret
)

TYPE_TO_KIND = {}
for resource_class in RESOURCE_CLASSES:
    TYPE_TO_KIND[resource_class.url_type] = resource_class.__name__

# inverse
KIND_TO_TYPE = {}
for kube_type in TYPE_TO_KIND:
    KIND_TO_TYPE[TYPE_TO_KIND[kube_type]] = kube_type


class Kubectl(KubeUtils):
    """Backward compatibility shim.  Don't use this for new
    projects.
    """
    def __init__(self, context=None, namespace=None, dryrun=None):
        super(Kubectl, self).__init__(context, namespace)

    def create_namespace(self, namespace):
        return Namespace(self).create(namespace)

    def create_path(self, path_or_fn):
        sh.kubectl.create(
            '--save-config',
            "-f {}".format(path_or_fn),
            context=self.config.context,
            namespace=self.config.namespace
        )

    def delete_path(self, path_or_fn):
        sh.kubectl.delete(
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

