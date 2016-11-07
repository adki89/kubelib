#!/usr/bin/python
# -*- coding: utf-8 -*-

import unittest

from context import kubelib
from munch import Munch
import sh
import stub

class BasicTestSuite(unittest.TestCase):
    """Basic test cases."""

    def setUp(self):
        self.kube = stub.KubeConfig()

    def test_does_it_import(self):
        assert True

    def test_getnamespaces(self):
        ns = kubelib.Namespace(self.kube).get_list()
        self.assertEqual(ns, [])

    # def test_createdestroy_namespaces(self):
    #     ns = self.kube.get_namespaces()
    #     self.assertEqual(ns, [])

    #     ns = self.kube.create_namespace("new_namespace")
    #     assert ns, "Namespace was _not_ created"

    #     ns = self.kube.get_namespaces()
    #     self.assertEqual(ns, ['new_namespace'])

    # def test_resource(self):
    #     pod = self.kube.get_resource('pod')
    #     pods = self.kube.get_resource('pods')

    #     self.assertEqual(pod, pods)


if __name__ == '__main__' and __package__ is None:
    from os import sys, path
    sys.path.append(path.dirname(path.dirname(path.abspath("."))))
    unittest.main()
