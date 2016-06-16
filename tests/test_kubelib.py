#!/usr/bin/python
# -*- coding: utf-8 -*-

import unittest

from context import kubelib
from stub import kubectl
from bunch import Bunch

class BasicTestSuite(unittest.TestCase):
    """Basic test cases."""

    def test_does_it_import(self):
        assert True

    def test_getnamespaces(self):
        kube = kubelib.Kubectl(context='my-context', namespace='my-namespace', dryrun=False)

        # override kubectl to use our stub
        kube.kubectl = kubectl

        ns = kube.get_namespaces()
        self.assertEqual(
            ns, [
                Bunch(
                    apiVersion='v1', 
                    kind='Namespace', 
                    metadata=Bunch(
                        creationTimestamp='2016-03-10T02:20:28Z', 
                        labels=Bunch(
                            name='bi'
                        ), 
                        name='bi', 
                        resourceVersion='123456', 
                        selfLink='/api/v1/namespaces/bi', 
                        uid='abcdefab-abcd-abcde-abcdefabcdef'), 
                    spec=Bunch(
                        finalizers=['kubernetes']
                    ), 
                    status=Bunch(
                        phase='Active'
                    )
                )
            ]
        )

if __name__ == '__main__' and __package__ is None:
    from os import sys, path
    sys.path.append(path.dirname(path.dirname(path.abspath("."))))
    unittest.main()
