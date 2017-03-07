"""Library of Kubernetes flavored helpers and objects."""

import base64
import json
import logging
import os
import shutil
import tempfile
import time

import glob2

import munch

import requests

import sh

import yaml


my_requests = requests

LOG = logging.getLogger(__name__)
logging.info('Starting...')


class TimeOut(Exception):
    """maximum timeout exceeded."""


class KubeError(Exception):
    """Generic Kubernetes error wrapper."""


class ContextRequired(KubeError):
    """Everything requires a context."""


class ClusterNotFound(KubeError):
    """Probably a bad ~/.kube/config."""


class KubeConfig(object):
    """Holds config information to communicate with a kube cluster."""

    def __init__(self, context=None, namespace=None):
        """Create a new KubeConfig object.

        This holds all the config information needed to communicate with
        a particular kubernetes cluster.

        :param context: Kubernetes context
        :param namespace: Kubernetes namespace
        """
        self.config = None
        with open(os.path.expanduser("~/.kube/config")) as handle:
            self.config = munch.Munch.fromYAML(
                handle.read()
            )

        self.context = None
        self.set_context(context)

        # if namespace is None:
        # default to the current kubectl namespace
        cluster_name = None

        self.context_obj = None
        for context_obj in self.config.contexts:
            if context_obj.name == self.context:
                self.context_obj = context_obj

        if self.context_obj is None:
            raise ContextRequired('Context %r not found' % self.context)

        self.namespace = None
        if hasattr(namespace, "metadata"):
            namespace = namespace.metadata.name

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

        self.req = None

        self.vault_client = None

    def set_context(self, context=None):
        """Set the current context.

        This is generally done during initialization.

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
        """Set the current namespace.

        This is generally done during initialization.

        Passing *None* will detect and utilize the current
        default kubectl namespace for this context.

        :param namespace: Namespace to use
        """
        if namespace is None:
            namespace = self.context_obj.context.get('namespace')

        self.namespace = namespace

    def set_vault(self, vault_client):
        """Helper to assign a pre-autheticated vault client.

        :param vault_client: hvac client
        """
        self.vault_client = vault_client


class KubeUtils(KubeConfig):
    """Child of KubeConfig with some helper functions attached."""

    def _get_container_names(self, resource_desc):
        containers = set()
        try:
            for container in resource_desc.spec.template.spec.containers:
                containers.add(container.name)

        except AttributeError:
            pass

        return containers

    def apply_path(self, path, recursive=False, replace_set=None):
        """Apply all the yaml files in `path` to the current context/namespace.

        Exactly what apply means depends on the resource type.

        :param path: Directory of yaml resources
        :param recursive: True to recurse into subdirectories
        :param reload_set: Set of containers that should be reloaded
        """
        path.rstrip('/')
        if replace_set is None:
            replace_set = set()

        if recursive:
            path += "/**/*"
        else:
            path += "/*"

        cache = {}
        for resource_fn in glob2.glob(path):
            LOG.info('Considering %r', resource_fn)
            if os.path.isdir(resource_fn):
                continue

            if not resource_fn.endswith(('.yml', '.yaml')):
                continue

            with open(resource_fn, 'r') as handle:
                resource_content = handle.read()
                resource_desc = munch.Munch.fromYAML(
                    resource_content
                )

            if resource_desc is None:
                LOG.error('Invalid resource file %s', resource_fn)
                continue

            if resource_desc.kind not in cache:
                resource_class = resource_by_kind(resource_desc.kind)
                resource = resource_class(self)
                cache[resource_desc.kind] = resource

            force = False
            if replace_set:
                container_names = self._get_container_names(resource_desc)

                LOG.info('Looking for %s in %s', container_names, replace_set)
                # at least some pods ought to be replaced instead of applied
                # TODO do a set intersection, nested loops is dumb
                for container_name in container_names:
                    if container_name in replace_set:
                        LOG.info('Forcing replacement of %s', resource_fn)
                        force = True
                        break
                    else:
                        LOG.info(
                            '%s is not in %s',
                            container_name,
                            container_names
                        )

            # there are configmap changes so we want to replace
            # the pod instead of just applying it.
            cache[resource_desc.kind].apply(
                resource_desc, resource_fn, force=force
            )

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

        while success is False and retry_count < max_retry:
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

        cont_id = pod_obj.status["containerStatuses"][0]["containerID"][9:21]

        command = "docker cp {origin} {container}:{destination}".format(
            origin=tempfn,
            container=cont_id,
            destination=destination_fn
        )

        sh.ssh(node_name, command)

    def delete_by_type(self, resource_type):
        """Loop through and destroy all resources of the given type.

        :param resource_type: resource type (Service, Pod, etc..)
        """
        resource = resource_by_kind(resource_type)(self)

        for resource_obj in resource.get_list():
            resource.delete(resource_obj.metadata.name)

    def clean_volumes(self):
        """Clean persistent volumes.

        Delete and rebuild all volumes in a released or failed state.
        Note: this Persistent Volumes are *not* contained by namespace
        so this is a whole-context operation.
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
    """Base class for communicating with Kubernetes.

    Provides both HTTP and a kubectl command line wrapper.
    """

    api_base = "/api/v1"

    def __init__(self, kubeconfig):
        """"Create a new Kubernetes object."""
        self.config = kubeconfig
        if self.config.req is None:
            self.client = requests.Session()
            self.client.cert = kubeconfig.cert
            self.client.verify = self.config.ca
        else:
            self.client = self.config.req

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

    def _patch(self, url, data):
        url = self.config.cluster.server + self.api_base + url
        LOG.debug('_path: %s : %s', url, data)
        response = self.client.patch(
            url,
            data=json.dumps(data),
            headers={'content-type': 'application/strategic-merge-patch+json'})
        return response.json()

    def _delete(self, url):
        url = self.config.cluster.server + self.api_base + url
        response = self.client.delete(url)
        return response.json()


class ActorBase(Kubernetes):
    """Base class for the Actors (the things that actually *do* stuff).

    Different kubernetes resource types need to be manage a bit
    differently.  The Actor sub-classes contain those differences.
    """

    url_type = None
    list_uri = "/namespaces/{namespace}/{resource_type}"
    single_uri = "/namespaces/{namespace}/{resource_type}/{name}"
    aliases = []
    cache = None
    secrets = False

    def get_list(self):
        """Retrieve a list of munch objects.

        Describing all the resources of this type in this namespace/context
        """
        url = self.list_uri.format(
            namespace=self.config.namespace,
            resource_type=self.url_type
        )
        resources = munch.munchify(
            self._get(url)
        )
        # LOG.debug('get_list resources: %r', resources)
        out = resources.get("items", [])
        if out is None:
            out = []
        return out

    def get(self, name):
        """Retrieve a single munch describing one resource by name."""
        return munch.munchify(
            self._get(self.single_uri.format(
                namespace=self.config.namespace,
                resource_type=self.url_type,
                name=name
            ))
        )

    def describe(self, name):
        """Return a yaml-ish text blob.

        Not helpful for automation, very helpful for humans.
        """
        try:
            return self.kubectl.describe(
                self.url_type,
                name,
                '--context={}'.format(self.config.context),
                '--namespace={}'.format(self.config.namespace)
            )
        except sh.ErrorReturnCode as err:
            logging.error("Unexpected response: %s", err)

    def patch(self, name, data):
        """Update one resource."""
        return self._patch(self.single_uri.format(
            namespace=self.config.namespace,
            resource_type=self.url_type,
            name=name
        ), data=data)

    def delete(self, name):
        """Delete the named resource.

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
            logging.error("Unexpected response: %s", err)

    def apply_file(self, path_or_fn):
        """Simple kubectl apply wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        LOG.info('(=) kubectl apply --record -f %s', path_or_fn)
        sh.kubectl.apply(
            '--record',
            "-f", path_or_fn,
            context=self.config.context,
            namespace=self.config.namespace
        )

    def replace_path(self, path_or_fn, force=False):
        """Simple kubectl replace wrapper.

        :param path_or_fn: Path or filename of yaml resource descriptions
        """
        LOG.info('(=) kubectl replace --force=%s -f %s', force, path_or_fn)
        self.kubectl.replace(
            '-f', path_or_fn,
            '--force={}'.format("true" if force else "false"),
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

    def apply(self, desc, filename, force=False):
        """NOOP Placeholder to be overridden by sub-classes.

        :param desc: Munch resource object
        :param filename: Filename associated with the resource
        """
        return set()

    def exists(self, name, force_reload=False):
        """Return True if *name* exist in kubes.

        :param name: Name of the resource we are interest in
        :param force_reload: Force a fresh cope of inventory (bypass cache)
        """
        fresh = False
        if self.cache is None or force_reload:
            fresh = True
            self.cache = {}
            res_list = self.get_list()
            for res in res_list:
                self.cache[res.metadata.name] = res

        url = self.list_uri.format(
            namespace=self.config.namespace,
            resource_type=self.url_type
        )

        if name in self.cache:
            LOG.info('(EXISTS) [%s] %s:%s', fresh, url, name)
            return True
        else:
            LOG.info('(MISSING) [%s] %s:%s', fresh, url, name)
            return False

    def get_secrets(self, container_name):
        """Retrieve secrets from vault for the named pod."""
        if self.config.vault_client is None:
            LOG.info('No Vault Client provided, skipping...')
            return

        secret_url = "/secret/{context}/{namespace}/{container}".format(
            context=self.config.context,
            namespace=self.config.namespace,
            container=container_name
        )
        LOG.info('Reading secrets for %r', secret_url)
        container_secrets = {}
        try:
            container_secrets = self.config.vault_client.read(secret_url)['data']
        except Exception as err:
            LOG.error(err)
            LOG.info('No secrets found for %s.  Skipping...', container_name)

        LOG.info('Found %s secrets', len(container_secrets))
        return container_secrets

    def simple_name(self, desc):
        """Override-able func to get the name of a resource."""
        return desc.metadata.name

    def build_env_secrets(self, pod_secrets, secret_name):
        """
        Convert Hashi secrets to Kube secrets.

        reformulate secrets to a dict suitable for
        creating kube secrets and a list for injecting
        into the .yaml
        """
        secrets = {}
        env = []
        envdict = {}
        # LOG.info(pod_secrets)
        for secret in pod_secrets:
            # LOG.info('pod_secrets[%r]: %r', secret, pod_secrets[secret])

            my_secret = json.loads(pod_secrets[secret])
            secrets[secret] = my_secret['value']
            if my_secret.get('type', '') == 'env':
                val = {
                    "name": my_secret['key'],
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": secret_name,
                            "key": secret
                        }
                    }
                }
                LOG.info('Adding secret: %r', val)

                env.append(val)
                envdict[my_secret['key']] = val

        return (env, envdict, secrets)

    def apply_secrets(self, desc, filename):
        """
        Inject secrets into the given resource.

        If this resource type support secrets and a vault client has
        been assigned we want to check vault to see if there are any
        secrets for this resource.  If there are we want to extract
        the secrets from vault and create them as kubernetes secrets.
        We're also going to patch into the resource yaml to make the
        secrets available as environmentals.

        TODO: support 'type's other than 'env' to automate putting
        files into the container.
        """
        changes = set()
        if self.secrets and self.config.vault_client:

            if os.environ.get('KUBELIB_VERSION', '1') == '2':
                # container based secrets
                if desc.kind in ["Ingress", ]:
                    secret_name = desc.metadata.name

                    default_secrets = self.get_secrets("_default_")
                    override_secrets = self.get_secrets(secret_name)

                    pod_secrets = default_secrets
                    for secret_key in override_secrets:
                        pod_secrets[secret_key] = override_secrets[secret_key]

                    env, envdict, secrets = self.build_env_secrets(
                        pod_secrets, secret_name
                    )

                    if secrets:
                        if Secret(self.config).exists(secret_name):
                            LOG.info(
                                'Secret %r exists.  Replacing keys: %s',
                                secret_name, secrets.keys()
                            )
                            # read "old" secrets (to get hashes)
                            old_secrets = Secret(self.config).get_list()

                            Secret(self.config).replace(secret_name, secrets)
                            # re-read secrets, compare hashes to "old" to
                            # determine if anything has changed.  Since they
                            # are encrypted this is the only real way
                            # to know.  Inefficient but secure.

                            new_secrets = Secret(self.config).get_list()

                            # TODO: is this a valid comparison?
                            if old_secrets != new_secrets:
                                LOG.info(
                                    'old_secrets != new_secrets\n%s\n%s',
                                    old_secrets,
                                    new_secrets
                                )
                                changes.add(secret_name)

                        else:
                            LOG.info(
                                'Secret %r does not exist.  Creating keys: %s',
                                secret_name, secrets.keys()
                            )
                            Secret(self.config).create(secret_name, secrets)
                    return

                for index, container in enumerate(
                    desc.spec.template.spec.containers
                ):
                    secret_name = container.name

                    default_secrets = self.get_secrets("_default_")
                    override_secrets = self.get_secrets(secret_name)

                    container_secrets = default_secrets
                    for secret_key in override_secrets:
                        container_secrets[secret_key] = override_secrets[secret_key]

                    env, envdict, secrets = self.build_env_secrets(
                        container_secrets, secret_name
                    )
                    # old env in pod.get("env", [])
                    # new env in myenv
                    # are they different?  Ifso, changes.add(secret_name)

                    if secrets:
                        if Secret(self.config).exists(secret_name):
                            LOG.info(
                                'Secret %r exists.  Replacing keys: %s',
                                secret_name, secrets.keys()
                            )

                            # read "old" secrets (to get hashes)
                            old_secrets = Secret(self.config).get_list()

                            Secret(self.config).replace(secret_name, secrets)

                            new_secrets = Secret(self.config).get_list()

                            # TODO: is this a valid comparison?
                            if old_secrets != new_secrets:
                                LOG.info(
                                    'Detected change in secrets (%s)',
                                    secret_name
                                )
                                changes.add(secret_name)
                        else:
                            LOG.info(
                                'Secret %r does not exist.  Creating keys: %s',
                                secret_name, secrets.keys()
                            )
                            Secret(self.config).create(secret_name, secrets)
                            changes.add(secret_name)

                    myenv = list(env)
                    for v in container.get("env", []):
                        if v.name in envdict:
                            LOG.info('Replacing env %s', v.name)
                        else:
                            LOG.info('Passing env %s through', v.name)
                            myenv.append(v.toDict())

                    reimage(
                        filename=filename,
                        xpath="spec.template.spec.containers.%i.env" % index,
                        newvalue=myenv
                    )

            else:
                # (obsolete) pod based secrets
                secret_name = desc.metadata.name + "-vault"

                default_secrets = self.get_secrets("_default_")
                override_secrets = self.get_secrets(desc.metadata.name)

                pod_secrets = default_secrets
                for secret_key in override_secrets:
                    pod_secrets[secret_key] = override_secrets[secret_key]

                env, envdict, secrets = self.build_env_secrets(
                    pod_secrets, secret_name
                )

                if secrets:
                    if Secret(self.config).exists(secret_name):
                        LOG.info(
                            'Secret %r already exists.  Replacing keys: %s',
                            secret_name, secrets.keys()
                        )
                        Secret(self.config).replace(secret_name, secrets)
                    else:
                        LOG.info(
                            'Secret %r does not exist.  Creating keys: %s',
                            secret_name, secrets.keys()
                        )
                        Secret(self.config).create(secret_name, secrets)

                # new secrets override old ones
                if "template" in desc.spec:
                    for index, container in enumerate(
                        desc.spec.template.spec.containers
                    ):
                        myenv = list(env)
                        # LOG.info('container: %s', container)
                        for v in container.get("env", []):
                            if v.name in envdict:
                                LOG.info('Replacing env %s', v.name)
                            else:
                                LOG.info('Passing env %s through', v.name)
                                myenv.append(v.toDict())

                        xpath = "spec.template.spec.containers.%i.env" % index
                        reimage(
                            filename=filename,
                            xpath=xpath,
                            newvalue=myenv
                        )

        return changes


class ReadMergeApplyActor(ActorBase):
    """
    Do a kubectl get, update with the contents of filename.

    write it, then apply it.
    """

    def apply(self, desc, filename, force=False):
        """Read, Merge then Apply."""
        changes = self.apply_secrets(desc, filename)

        if self.exists(desc.metadata.name):
            # pull from the server
            remote = munch.Munch()

            try:
                remote = self.get(desc.metadata.name)
            except Exception as err:
                LOG.error(
                    '%s failure to retrieve existing resource %s',
                    err,
                    filename
                )

            # merge our file on top of it
            remote.update(desc)

            # write to disk
            with open(filename, 'w') as h:
                h.write(remote.toJSON())

        try:
            if force or len(changes):
                if changes:
                    LOG.info(
                        'Secret changes detected: %s -- Replacing pod',
                        changes
                    )

                if desc.kind in ['Deployment']:
                    # even with force=true replacing
                    # a deployment doesn't cleanup and
                    # redeploy pods.
                    self.delete_path(filename)
                    self.create_path(filename)
                else:

                    self.replace_path(filename, force=force)
            else:
                self.apply_file(filename)

        except sh.ErrorReturnCode_1:
            LOG.error('apply_file failed (forcing)')

            if self.exists(desc.metadata.name):
                self.delete_path(filename)
            self.create_path(filename)

        return changes


class ApplyActor(ActorBase):
    """Do a kubectl apply on the resource."""

    def apply(self, desc, filename, force=False):
        """Simple apply with secrets support."""
        changes = self.apply_secrets(desc, filename)
        action = "unknown"
        try:
            if force or len(changes):
                LOG.info(
                    'Secret changes detected: %s -- Replacing pod',
                    changes
                )
                action = "replace_path"
                self.replace_path(filename, force=force)
            else:
                action = "apply_file"
                self.apply_file(filename)

        except sh.ErrorReturnCode_1:
            LOG.error('%s failed (forcing)', action)

            if self.exists(desc.metadata.name):
                self.delete_path(filename)
            self.create_path(filename)

        return changes


class DeleteCreateActor(ActorBase):
    """Delete the resource and re-create it."""

    def apply(self, desc, filename, force=False):
        """Delete then create."""
        changes = self.apply_secrets(desc, filename)

        if self.exists(desc.metadata.name):
            self.delete_path(filename)
        self.create_path(filename)

        return changes


class ReplaceActor(ActorBase):
    """Do a *kubectl replace* on this object."""

    def apply(self, desc, filename, force=False):
        """Replace if it exists, Create if it doesn't."""
        changes = self.apply_secrets(desc, filename)

        if self.exists(desc.metadata.name):
            if changes:
                force = True

            if force and desc.kind in ['Deployment']:
                # even with force=true replacing
                # a deployment doesn't cleanup and
                # redeploy pods.
                self.delete_path(filename)
                self.create_path(filename)
            else:
                self.replace_path(filename, force=force)
        else:
            self.create_path(filename)

        return changes


class CreateIfMissingActor(ActorBase):
    """Create only if the resource is missing."""

    def apply(self, desc, filename, force=False):
        """Create only.  You almost never want this."""
        changes = self.apply_secrets(desc, filename)

        if not self.exists(desc.metadata.name):
            self.create_path(filename)

        return changes


class IgnoreActor(ActorBase):
    """NOOP, don't do anything."""

########################################


class ConfigMap(ReplaceActor):
    """ConfigMap Resource.

    The ConfigMap API resource holds key-value pairs of configuration
    data that can be consumed in pods or used to store configuration data
    for system components such as controllers. ConfigMap is similar to
    Secrets, but designed to more conveniently support working with
    strings that do not contain sensitive information.
    """

    url_type = 'configmaps'

    def from_path(self, configkey, filename):
        """File contents should be one config key/value per line delimited by an equal.

        ala:

        enemies=aliens
        lives=3
        enemies.cheat=true
        enemies.cheat.level=noGoodRotten

        # When --from-file points to a directory, each file directly in that
        # directory is used to populate a key in the ConfigMap, where the name
        # of the key is the filename, and the value of the key is the content
        # of the file.
        """
        self.kubectl.create.configmap(
            configkey,
            "--from-file={}".format(filename),
            '--context={}'.format(self.config.context),
            '--namespace={}'.format(self.config.namespace)
        )

    def from_dict(self, configkey, literal_dict):
        """Turn a dict into a configmap."""
        tdir = tempfile.mkdtemp()
        for key in literal_dict:
            with open(os.path.join(tdir, key), 'w') as h:
                h.write(literal_dict[key])

        max_timeout = 120
        retry_delay = 1
        success = False
        start = time.time()

        while success is False and time.time() < start + max_timeout:
            try:
                self.kubectl.create.configmap(
                    configkey,
                    "--from-file={}".format(tdir),
                    '--context={}'.format(self.config.context),
                    '--namespace={}'.format(self.config.namespace)
                )
                success = True
            except sh.ErrorReturnCode_1 as err:
                LOG.error("Error %s creating configmap", err)
                time.sleep(
                    min(
                        retry_delay,
                        (max_timeout - (time.time() - start))
                    )
                )
                retry_delay = retry_delay * 2

        shutil.rmtree(tdir)


class Deployment(ReplaceActor):
    """Deployment resource.

    A Deployment provides declarative updates for Pods and
    Replica Sets (the next-generation Replication Controller).
    You only need to describe the desired state in a Deployment
    object, and the Deployment controller will change the actual
    state to the desired state at a controlled rate for you. You
    can define Deployments to create new resources, or replace
    existing ones by new ones.
    """

    url_type = 'deployments'
    api_base = "/apis/extensions/v1beta1"
    secrets = True


class DaemonSet(ReplaceActor):
    """DaemonSet resource.

    A Daemon Set ensures that all (or some) nodes run a copy of a
    pod. As nodes are added to the cluster, pods are added to them. As
    nodes are removed from the cluster, those pods are garbage collected.
    Deleting a Daemon Set will clean up the pods it created.
    """

    url_type = "daemonsets"
    api_base = "/apis/extensions/v1beta1"


class HorizontalPodAutoscaler(CreateIfMissingActor):
    """HorizontalPodAutoscaler resource.

    With Horizontal Pod Autoscaling, Kubernetes automatically scales the
    number of pods in a replication controller, deployment or replica set
    based on observed CPU utilization (or, with alpha support, on some other,
    application-provided metrics).
    The Horizontal Pod Autoscaler is implemented as a Kubernetes API resource
    and a controller. The resource determines the behavior of the controller.
    The controller periodically adjusts the number of replicas in a replication
    controller or deployment to match the observed average CPU utilization to
    the target specified by user.
    """

    url_type = "horizontalpodautoscalers"
    api_base = "/apis/autoscaling/v1"


class Ingress(ReplaceActor):
    """Ingress resource.

    An Ingress is a collection of rules that allow inbound
    connections to reach the cluster services.
    """

    url_type = "ingresses"
    aliases = ["ing"]
    api_base = "/apis/extensions/v1beta1"
    secrets = True


class Job(DeleteCreateActor):
    """Job resource."""

    url_type = "jobs"
    api_base = "/apis/extensions/v1beta1"
    secrets = True


class LimitRange(ReplaceActor):
    """LimitRange resource."""

    url_type = "limitranges"
    aliases = ["limit"]
    secrets = False

    def create(self, limits):
        """Helper to create limits on the namespace."""
        # response =
        self._post(
            "/namespaces/{namespace}/limitranges".format(
                namespace=self.config.namespace
            ),
            data={
                "kind": "LimitRange",
                "apiVersion": "v1",
                "metadata": {
                    "name": "core-resource-limits",
                },
                "spec": {
                    "limits": limits
                }
            }
        )

        # great.  there isn't a status in the response.
        # if 'status' not in response:

        #     raise KubeError(
        #         'Expected response to include "status" got %s' % response
        #     )
        # elif response['status'] == "Failure":
        #     # this is where we land if the limitrange already exists
        #     # LOG.error(response)
        #     # raise KubeError('Failed to create namespace limitrange')
        #     return False

        return True


class Namespace(CreateIfMissingActor):
    """Namespace resource.

    Kubernetes supports multiple virtual clusters backed by the same
    physical cluster. These virtual clusters are called namespaces.
    """

    url_type = "namespaces"
    aliases = ['ns']
    list_uri = "/{resource_type}"
    single_uri = "/{resource_type}/{name}"

    def create(self, namespace):
        """Create the given namespace.

        :param namespace: name of the namespace we want to create
        :returns: True if the create succeeded, False otherwise
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
                LOG.error('(ignoring)')

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


class NetworkPolicy(ReplaceActor):
    """NetworkPolicy resource.

    A network policy is a specification of how selections of pods are
    allowed to communicate with each other and other network endpoints.
    NetworkPolicy resources use labels to select pods and define whitelist
    rules which allow traffic to the selected pods in addition to what is
    allowed by the isolation policy for a given namespace.
    """

    url_type = "networkpolicies"
    api_base = "/apis/extensions/v1beta1"


class Node(IgnoreActor):
    """Node resource.

    Node is a worker machine in Kubernetes, previously known as Minion.
    Node may be a VM or physical machine, depending on the cluster. Each
    node has the services necessary to run Pods and is managed by the
    master components. The services on a node include docker, kubelet
    and network proxy. See The Kubernetes Node section in the architecture
    design doc for more details.
    """

    url_type = "nodes"
    aliases = ['node']


class PersistentVolume(CreateIfMissingActor):
    """PersistentVolume resource.

    A PersistentVolume (PV) is a piece of networked storage in the cluster
    that has been provisioned by an administrator. It is a resource in the
    cluster just like a node is a cluster resource. PVs are volume plugins
    like Volumes, but have a lifecycle independent of any individual pod that
    uses the PV. This API object captures the details of the implementation of
    the storage, be that NFS, iSCSI, or a cloud-provider-specific storage
    system.
    """

    url_type = "persistentvolumes"
    aliases = ['pv']
    list_uri = "/{resource_type}"
    single_uri = "/{resource_type}/{name}"

    def set_claim_ref(self, name, claimref):
        """Manually link PV $name with the given claim reference (pvc name)."""
        return self.patch(
            name,
            {
                "spec": {
                    "claimRef": {
                        "name": claimref,
                        "namespace": self.config.namespace
                    }
                }
            }
        )

    set_claimRef = set_claim_ref


class PersistentVolumeClaim(CreateIfMissingActor):
    """PersistentVolumeClaim resource.

    A PersistentVolumeClaim (PVC) is a request for storage by a user. It
    is similar to a pod. Pods consume node resources and PVCs consume PV
    resources. Pods can request specific levels of resources (CPU and Memory).
    Claims can request specific size and access modes (e.g, can be mounted once
    read/write or many times read-only).
    """

    url_type = "persistentvolumeclaims"
    aliases = ['pvc']


class PetSet(DeleteCreateActor):
    """PetSet resource.

    In Kubernetes, most pod management abstractions group them into
    disposable units of work that compose a micro service. Replication
    controllers for example, are designed with a weak guarantee - that
    there should be N replicas of a particular pod template. The pods
    are treated as stateless units, if one of them is unhealthy or
    superseded by a newer version, the system just disposes it.

    A Pet Set, in contrast, is a group of stateful pods that require a
    stronger notion of identity. The document refers to these as "clustered
    applications".

    The co-ordinated deployment of clustered applications is notoriously
    hard. They require stronger notions of identity and membership,
    which they use in opaque internal protocols, and are especially
    prone to race conditions and deadlock. Traditionally administrators
    have deployed these applications by leveraging nodes as stable,
    long-lived entities with persistent storage and static ips.

    The goal of Pet Set is to decouple this dependency by assigning
    identities to individual instances of an application that are not
    anchored to the underlying physical infrastructure.
    """

    url_type = "petsets"
    api_base = "/apis/apps/v1alpha1"


class Pod(IgnoreActor):
    """Pod resource.

    A pod (as in a pod of whales or pea pod) is a group of one or more
    containers (such as Docker containers), the shared storage for those
    containers, and options about how to run the containers. Pods are always
    co-located and co-scheduled, and run in a shared context. A pod models an
    application-specific "logical host" - it contains one or more application
    containers which are relatively tightly coupled - in a pre-container world,
    they would have executed on the same physical or virtual machine.
    """

    url_type = "pods"
    aliases = ['pod']

    def simple_name(self, desc):
        """Getting a pods base name is disturbingly tricky."""
        return desc.metadata.generateName.split('-')[0]

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
                if pod.metadata.generateName.split('-')[0] == pod_name:
                    if pod.status.phase == "Running":
                        return pod.metadata.name
                    else:
                        LOG.info(
                            "Container %s found but status is %s",
                            pod.metadata.name,
                            pod.status.phase
                        )
                else:
                    LOG.debug('%s != %s', pod_name, pod.metadata.generateName)

            # ok, we got through the list of all pods without finding
            # an acceptable running pod.  So we pause (briefly) and try again.
            right_now = time.time()
            if (right_now - start) > max_delay:
                raise TimeOut('Maximum delay {} exceeded'.format(max_delay))
            else:
                LOG.info('%s <= %s, waiting...', right_now - start, max_delay)
                time.sleep(2)

    def exec_cmd(self, pod, container, *command):
        """Execute a command in a pod/container.

        If container is None the kubectl behavior is to pick the
        first container if possible
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
    """ReplicationController resource.

    A replication controller ensures that a specified number of pod
    "replicas" are running at any one time. In other words, a replication
    controller makes sure that a pod or homogeneous set of pods are always up
    and available. If there are too many pods, it will kill some. If there are
    too few, the replication controller will start more. Unlike manually
    created pods, the pods maintained by a replication controller are
    automatically replaced if they fail, get deleted, or are terminated. For
    example, your pods get re-created on a node after disruptive maintenance
    such as a kernel upgrade. For this reason, we recommend that you use a
    replication controller even if your application requires only a single pod.
    You can think of a replication controller as something similar to a process
    supervisor, but rather than individual processes on a single node, the
    replication controller supervises multiple pods across multiple nodes.
    """

    url_type = "replicationcontrollers"
    secrets = True


class Role(CreateIfMissingActor):
    """Role resource.

    Roles hold a logical grouping of permissions. These permissions map very
    closely to ABAC policies, but only contain information about requests being
    made. Permission are purely additive, rules may only omit permissions they
    do not wish to grant.
    """

    url_type = "roles"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"


class ServiceAccount(CreateIfMissingActor):
    """ServiceAccount resource."""

    url_type = "serviceaccounts"

    def create(self, name="default"):
        """Create a new ServiceAccount."""
        return self.kubectl.create.serviceaccount(
            name,
            namespace=self.config.namespace
        )


class ClusterRole(CreateIfMissingActor):
    """ClusterRole resource.

    ClusterRoles hold the same information as a Role but can apply to any
    namespace as well as non-namespaced resources (such as Nodes,
    PersistentVolume, etc.)
    """

    url_type = "clusterroles"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"


class RoleBinding(CreateIfMissingActor):
    """RoleBinding resource.

    RoleBindings perform the task of granting the permission to a user or
    set of users. They hold a list of subjects which they apply to, and a
    reference to the Role being assigned.

    RoleBindings may also refer to a ClusterRole. However, a RoleBinding that
    refers to a ClusterRole only applies in the RoleBinding's namespace, not at
    the cluster level. This allows admins to define a set of common roles for
    the entire cluster, then reuse them in multiple namespaces.
    """

    url_type = "rolebindings"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"


class ClusterRoleBinding(CreateIfMissingActor):
    """ClusterRoleBinding resource.

    A ClusterRoleBinding may be used to grant permissions in all namespaces.
    """

    url_type = "clusterrolebindings"
    api_base = "/apis/rbac.authorization.k8s.io/v1alpha1"
    list_uri = "/{resource_type}"


class Service(ReadMergeApplyActor):
    """Service resource.

    Kubernetes Pods are mortal. They are born and they die, and they
    are not resurrected. ReplicationControllers in particular create and
    destroy Pods dynamically (e.g. when scaling up or down or when doing
    rolling updates). While each Pod gets its own IP address, even those
    IP addresses cannot be relied upon to be stable over time. This leads
    to a problem: if some set of Pods (let's call them backends) provides
    functionality to other Pods (let's call them frontends) inside the
    Kubernetes cluster, how do those frontends find out and keep track of
    which backends are in that set?
    """

    url_type = "services"


class Secret(ReplaceActor):
    """Secret resource.

    Objects of type secret are intended to hold sensitive information,
    such as passwords, OAuth tokens, and ssh keys. Putting this information
    in a secret is safer and more flexible than putting it verbatim in a
    pod definition or in a docker image. See Secrets design document for
    more information.
    """

    url_type = "secrets"

    def create(self, name, dict_of_secrets):
        """Create a new secret(s)."""
        encoded_dict = {}
        for key in dict_of_secrets:
            encoded_dict[key] = base64.b64encode(str(dict_of_secrets[key]))

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
        """Replace an existing secret(s)."""
        encoded_dict = {}
        for key in dict_of_secrets:
            encoded_dict[key] = base64.b64encode(str(dict_of_secrets[key]))

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
    """Filter to lookup a resource by the `kind` field."""
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
    """Apply all the yaml files in `path` to the given context/namespace.

    Exactly what apply means depends on the resource type.

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
    """Loop through and destroy all resources of the given type.

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
    Replace the given xpath in the yaml filename with the value newvalue.

    >>> reimage(
        "./tests/reimage_test.yml",
        "alpha.beta.gamma.0.delta",
        "epsilon",
        "./tests/reimage_test_done.yml"
    )
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
    # resources
    ConfigMap,
    DaemonSet,
    Deployment,
    HorizontalPodAutoscaler,
    Ingress,
    Job,
    LimitRange,
    Namespace,
    Node,
    PersistentVolume,
    PersistentVolumeClaim,
    PetSet,
    Pod,
    ReplicationController,
    Secret,
    Service,
    # RBAC
    ClusterRole,
    ClusterRoleBinding,
    NetworkPolicy,
    Role,
    RoleBinding,
)

TYPE_TO_KIND = {}
for resource_class in RESOURCE_CLASSES:
    TYPE_TO_KIND[resource_class.url_type] = resource_class.__name__

# inverse
KIND_TO_TYPE = {}
for kube_type in TYPE_TO_KIND:
    KIND_TO_TYPE[TYPE_TO_KIND[kube_type]] = kube_type


class Kubectl(KubeUtils):
    """Backward compatibility shim.

    Don't use this for new projects.
    """

    def __init__(self, context=None, namespace=None, dryrun=None):
        """Wrapper to upstream __init__."""
        super(Kubectl, self).__init__(context, namespace)

    def create_namespace(self, namespace):
        """Create a new namespace."""
        return Namespace(self).create(namespace)

    def create_path(self, path_or_fn):
        """Create the given path/fn."""
        sh.kubectl.create(
            '--save-config',
            "-f {}".format(path_or_fn),
            context=self.config.context,
            namespace=self.config.namespace
        )

    def delete_path(self, path_or_fn):
        """Delete the given path/fn."""
        sh.kubectl.delete(
            "-f {}".format(path_or_fn),
            context=self.config.context,
            namespace=self.config.namespace
        )

    def create_if_missing(self, friendly_resource_type, glob_path):
        """Deprecated.  Create the given resource if it is missing."""
        cache = {}
        for resource_fn in glob2.glob(glob_path):
            with open(resource_fn) as handle:
                resource_desc = munch.Munch.fromYAML(
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
                'petset': '',
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

            if not cache[resource_desc.kind].exists(
                resource_desc.metadata.name
            ):
                self.create_path(resource_fn)
