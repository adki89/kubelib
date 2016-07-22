kubelib
=======

Python library to simplify kubernetes scripting.  Minimal test coverage.

`Full Documentation Here <http://public.safarilab.com/kubelib/>`_

Quickstart
----------

Import Kubelib and config::

	import kubelib
	kube = kubelib.KubeConfig(context='dev-seb', namespace='myspace')

List all namespaces::

	for ns in kubelib.Namespace(kube).get_list():
		print(ns.metadata.name)

List all resource controllers::

    for ns in kubelib.ReplicationController(kube).get_list():
        print(ns.metadata.name)

(you get the idea)

Get a specific pod::

    pod = kubelib.Pod(kube).get(podname)
    print(pod.toJSON())

Upgrade kubernetes based on a directory of yaml files::

    kube.apply_path("./kubernetes", recursive=True)

------

Initial package setup borrowed from `https://github.com/kennethreitz/samplemod`

A reasonable approach to getting sphinx output into github pages from `https://daler.github.io/sphinxdoc-test/includeme.html`
