import glob
import sh
import yaml
import bunch
import time

import logging
logger = logging.getLogger(__name__)

#: Mapping of kubernetes resource types (pvc, pods, service, etc..) to the
#: strings that Kubernetes wants in .yaml file 'Kind' fields.
TYPE_TO_KIND = {
    'pvc': 'PersistentVolumeClaim',
    'services': "Service",
}


class TimeOut(exception):
    """maximum timeout exceeded"""


class Kubectl(object):
    """Wrapper around the kubectl command line utility."""

    def __init__(self, context=None, namespace=None, dryrun=False):
        """Create new Kubectl object.

        :params context: Kubernetes context for this object
        :params namespace: Kubernetes namespace, defaults to the current default
        :params dryrun: When truthy don't actually send anything to kubectl
        """
        #: Kubernetes context
        if context is None:
            # default to the current context
            context = sh.kubectl.config('current-context').stdout.strip()

        self.context = context

        #: Kubernetes namespace
        if namespace is None:
            # default to the current kubectl namespace
            config = json.loads(sh.kubectl.config.view("-o", "json").stdout)
            for c in config['contexts']:
                if c['name'] == context:
                    namespace = c['context'].get('namespace')

        self.namespace = namespace

        #: Boolean indicating if we don't want to actually send
        #: anything to kubectl
        self.dryrun = dryrun

        if dryrun:
            self.kubectl = sh.echo
        else:
            self.kubectl = sh.kubectl

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
        """Return list of namespace objects."""
        self.namespaces = []
        namespaces_base = yaml.load(self.kubectl.get.namespaces('-o', 'yaml').stdout)
        try:
            for ns in namespaces_base['items']:
                self.namespaces.append(bunch.bunchify(ns))
        except TypeError:
            return []
        return self.namespaces

    def create_namespace(self, namespace):
        """Create the given namespace.

        :param namespace: name of the namespace we want to create
        :returns: True if the create succeeded, False otherwise (it already exists)
        """
        self.namespace = namespace
        try:
            self.kubectl.create.namespace(namespace)
            return True
        except sh.ErrorReturnCode:
            return False        

    # generic 

    def get_resource(self, resource_type, single=None):
        if single is None:
            resource_list = []
            resources = yaml.loads(sh.kubectl.get(
                resource_type,
                '--namespace', self.namespace,
                '--context', self.context,
                single,
                '--output', 'yaml'
            ).stdout)
            for resource in resources:
                resource_list.append(bunch.bunchify(resource))
            return resource_list

        else:
            resource = yaml.loads(sh.kubectl.get(
                resource_type,
                '--namespace', self.namespace,
                '--context', self.context,
                single,
                '--output', 'yaml'
            ).stdout)

            return bunch.bunchify(resource)      

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
        all_resources = yaml.load(
            self.kubectl.get(
                resource_type,
                '--namespace={}'.format(self.namespace),
                '--context={}'.format(self.context),
                '-o', 'yaml'            
            ).stdout
        )['items']

        setattr(self, resource_type, {})
        for resource in all_resources:
            self_dict = getattr(self, resource_type)
            self_dict[resource['metadata']['name']] = resource

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
        for resource in yaml.load(self.kubectl.get(
            resource_type,
            '--context={}'.format(self.context),
            '--namespace={}'.format(self.namespace),
            '-o', 'yaml'
        ).stdout)['items']:
            resource_name = resource['metadata']['name']
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
            if pv.status['phase'] in ['Released', 'Failed']:
                logger.info('Rebuilding PV %s', pv.metadata['name'])
                sh.kubectl.delete.pv(pv.metadata['name'], context=self.context)
                sh.kubectl.create(context=self.context, _in=yaml.dumps(pv))