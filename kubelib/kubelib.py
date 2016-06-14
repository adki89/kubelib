import glob
import sh
import yaml
import bunch

import logging
logger = logging.getLogger(__name__)

# Mapping of kubernetes resource types (pvc, pods, service, etc..) to the
# strings that Kubernetes wants in .yaml file 'Kind' fields.
TYPE_TO_KIND = {
    'pvc': 'PersistentVolumeClaim',
    'services': "Service",
}

class Kubectl(object):

    def __init__(self, context, namespace=None, dryrun=False):
        self.context = context
        self.namespace = namespace
        self.dryrun = dryrun

        if dryrun:
            self.kubectl = sh.echo
        else:
            self.kubectl = sh.kubectl

    def get_namespaces(self):
        self.namespaces = {}
        namespaces_base = yaml.load(self.kubectl.get.namespaces('-o', 'yaml').stdout)
        try:
            for ns in namespaces_base['items']:
                self.namespaces[ns['metadata']['name']] = bunch.bunchify(ns)
        except TypeError:
            return []
        return self.namespaces

    def create_namespace(self, namespace):
        """Create the given namespace."""
        self.namespace = namespace
        try:
            self.kubectl.create.namespace(namespace)
            return True
        except sh.ErrorReturnCode:
            return False        

    def create_path(self, path_or_fn):
        """Simple kubectl create wrapper"""
        self.kubectl.create(
            '-f', path_or_fn,
            '--namespace={}'.format(self.namespace),
            '--context={}'.format(self.context)
        )

    def delete_path(self, path_or_fn):
        """Simple kubectl delete wrapper"""
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
        """Make sure expected resources exist."""
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
        """loop through and destroy all resources of the given type."""
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
                    '--context={}'.format(context),
                    '--namespace={}'.format(namespace)
                )
            except sh.ErrorReturnCode as err:
                logging.error("Unexpected response: %r", err)
        