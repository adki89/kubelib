import yaml
import hashlib
import urllib
import requests
import kubelib
import os
import json 


class RequestResponse(object):
    def __init__(self, content):
        self.content = content

    def json(self):
        return json.loads(self.content)


class RequestsSession(object):
    """stub of requests"""

    def __init__(self, cert, ca):
        self.cert = cert
        self.ca = ca

        self.upstream = requests.Session()
        self.upstream.cert = cert
        self.upstream.verify = ca
    
    def get(self, url, **kwargs):

        # hash url and kwargs
        # is this hash in baked?
        # yes?  fetch it
        # no?  try and do it for real.
        hash = hashlib.sha256(
            url + urllib.urlencode(kwargs)
        ).hexdigest()

        cfn = "./tests/baked/{}.bake".format(hash)
        if not os.path.exists(cfn):
            response = self.upstream.get(url, **kwargs) 

            with open(cfn, "w") as h:
                h.write(json.dumps(response.json()))

        with open(cfn, "r") as h:
            content = h.read()

        return RequestResponse(content)


class KubeConfig(object):
    """Stub kubeconfig"""

    def __init__(self, context, namespace):
        self.upstream = kubelib.KubeConfig(context, namespace)
        self.req = RequestsSession(self.upstream.cert, self.upstream.ca)

        self.namespace = "fakenamespace"
        self.cluster = self.upstream.cluster


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
