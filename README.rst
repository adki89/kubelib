kubelib
=======

Python library to simplify kubernetes scripting

`Full Documentation Here <http://public.safarilab.com/kubelib/>`_

Quickstart
----------

List all namespaces::
	
	import kubelib

	kube = kubelib.Kubectl(context='sfo-context')

	for ns in kube.get_namespaces():
		print(ns.metadata.name)

------

Initial package setup borrowed from `https://github.com/kennethreitz/samplemod`

A reasonable approach to getting sphinx output into github pages from `https://daler.github.io/sphinxdoc-test/includeme.html`
