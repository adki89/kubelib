#!/usr/bin/python
# -*- coding: utf-8 -*-

import unittest

try:
    import kubelib
except ImportError:
    pass

import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.info('Starting tests...')


class RealTestSuite(unittest.TestCase):
    """Basic test cases against "real" kubernets.

    Since we don't know what to expect from many
    of these calls we can't assert, but this
    suite should still be very helpful in diagnosing
    issues or capturing example output to include
    in our stub.
    """

    def test_does_it_import(self):
        assert True

    def test_getnamespaces(self):
        kube = kubelib.Kubectl()
        ns = kube.get_namespaces()
        logger.info(ns)

if __name__ == '__main__' and __package__ is None:
    from os import sys, path
    sys.path.append(path.dirname(path.abspath("../kubelib")))

    import kubelib
    unittest.main()
