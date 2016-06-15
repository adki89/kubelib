import glob
import sh
import yaml
import bunch

import logging
logger = logging.getLogger(__name__)

#: Mapping of kubernetes resource types (pvc, pods, service, etc..) to the
#: strings that Kubernetes wants in .yaml file 'Kind' fields.
TYPE_TO_KIND = {
    'pvc': 'PersistentVolumeClaim',
    'services': "Service",
}

class Kubectl(object):
    """Wrapper around the kubectl command line utility."""

    def __init__(self, context, namespace=None, dryrun=False):
        """Create new Kubectl object.

        :params context: Kubernetes context for this object
        :params namespace: Kubernetes namespace, this is required (sorry)
        :params dryrun: When truthy don't actually send anything to kubectl
        """
        #: Kubernetes context
        self.context = context

        #: Kubernetes namespace
        self.namespace = namespace

        #: Boolean indicating if we don't want to actually send
        #: anything to kubectl
        self.dryrun = dryrun

        if dryrun:
            self.kubectl = sh.echo
        else:
            self.kubectl = sh.kubectl

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
        """
        self.namespace = namespace
        try:
            self.kubectl.create.namespace(namespace)
            return True
        except sh.ErrorReturnCode:
            return False        

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
        