import yaml


class KubeCluster(object):
    server = "http://localhost"

class KubeConfig(object):
    """Stub kubeconfig"""
    cert = None
    ca = None

    namespace = "fake-namespace"
    cluster = KubeCluster()


class kubectl_get(object):
	"""object get-er"""
	def namespaces(self, *args, **kwargs):
		return yaml.dump({
			'apiVersion': 'v1',
			'items': [
				{
					'apiVersion': 'v1',
					'kind': 'Namespace',
					'metadata': {
						'creationTimestamp': '2016-03-10T02:20:28Z',
						'labels': {
							'name': 'bi'
						},
						'name': 'bi',
						'resourceVersion': "123456",
						'selfLink': '/api/v1/namespaces/bi',
						'uid': 'abcdefab-abcd-abcde-abcdefabcdef'
					},
					'spec': {
						'finalizers': [
							"kubernetes"
						]
					},
					'status': {
						'phase': 'Active'
					}
				}
			]
		})


class kubectl(object):
	"""Stub of kubectl that should behave like
	sh.kubectl
	"""

	get = kubectl_get()
