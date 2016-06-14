# -*- coding: utf-8 -*-

import unittest

from .context import kubelib


class BasicTestSuite(unittest.TestCase):
    """Basic test cases."""

    def test_does_it_import(self):
        assert True


if __name__ == '__main__':
    unittest.main()