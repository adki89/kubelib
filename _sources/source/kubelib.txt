Kubelib
*******

kubelib is primarily a wrapper around the kubernetes kubectl command.  It is intended to simplify scripting jenkins tasks.  It is purely client side; nothing needs to be installed on the node or pods.

Most functions require a context and namespace.  If you have defaults set these will be used if values are not provided.  In practice I highly recommend explicitely providing both context and namespace.

This module makes use of the "bunch" python module for object return values.

https://github.com/dsc/bunch

tldr: you can use them as dictionaries (ala pod['metadata']['name']) or with attributes (ala pod.metadata.name).  I think this makes code with deeply nested dictionaries easier to read.


.. automodule:: kubelib

	.. autoclass:: kubelib.Kubectl
		:member-order: groupwise
