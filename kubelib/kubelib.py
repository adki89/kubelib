import glob
import sh
import yaml

# Mapping of kubernetes resource types (pvc, pods, service, etc..) to the
# strings that Kubernetes wants in .yaml file 'Kind' fields.
TYPE_TO_KIND = {
    'pvc': 'PersistentVolumeClaim',
    'services': "Service",
}

class Kubectl(object):

    def __init__(self, context):
        self.context = context

    def create_namespace(self, namespace):
        """Create the given namespace."""
       
        self.namespace = namespace

        try:
            sh.kubectl.create.namespace(namespace)
            return True
        except sh.ErrorReturnCode:
            return False        

    def create_path(self, path_or_fn):
        """Simple kubectl create wrapper"""
        sh.kubectl.create(
            '-f', path_or_fn,
            '--namespace={}'.format(self.namespace),
            '--context={}'.format(self.context)
        )

    def delete_path(self, path_or_fn):
        """Simple kubectl delete wrapper"""
        try:
            sh.kubectl.delete(
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
            sh.kubectl.get(
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
                    print('Creating {}'.format(resource_fn))
                    self.create_path(resource_fn)
                    # placeholder
                    getattr(self, resource_type)[resource['metadata']['name']] = True

    def destroy_by_type(self, resource_type):
        """loop through and destroy all resources of the given type."""
        for resource in yaml.load(sh.kubectl.get(
            resource_type,
            '--context={}'.format(self.context),
            '--namespace={}'.format(self.namespace),
            '-o', 'yaml'
        ).stdout)['items']:
            resource_name = resource['metadata']['name']
            print('kubectl delete {} {}'.format(resource_type, resource_name))
            
            try:
                sh.kubectl.delete(
                    resource_type,
                    resource_name, 
                    '--context={}'.format(context),
                    '--namespace={}'.format(namespace)
                )
            except sh.ErrorReturnCode as err:
                print("Unexpected response: %r" % err)
        