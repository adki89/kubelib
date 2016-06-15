# -*- coding: utf-8 -*-

import unittest

from .context import kubelib


class BasicTestSuite(unittest.TestCase):
    """Basic test cases."""

    def test_does_it_import(self):
        assert True

    def test_getnamespaces(self):
    	kube = kubelib.Kubectl(context='my-context', namespace='my-namespace', dryrun=True)
    	kube.get_namespaces()


if __name__ == '__main__':
    unittest.main()
