kubelib
=======

Python library to simplify kubernetes scripting.  Minimal test coverage.

`Full Documentation Here <http://public.safarilab.com/kubelib/>`_

TODO: The current plan is to rebuild this around <https://github.com/kubernetes-incubator/client-python>.

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


Upgrading Kubernetes
--------------------

Upgrade kubernetes based on a directory of yaml files::

    import kubelib
    kube = kubelib.KubeConfig(context='dev-seb', namespace='myspace')
    kube.apply_path("./kubernetes", recursive=True)

This will look at every yaml file and act based on the "Kind" field.  Deployments are replaced, replication controllers are deleted and re-created.  Other "Kind" resources are created if a resource with that "Kind" and "Name" is not already present.

Command Line Utilities
----------------------

This package provides a few command line utilities, the most helpful (to me) is `see_limits` which displays the resource limits for all pods and namespaces within a context.


------

Initial package setup borrowed from `https://github.com/kennethreitz/samplemod`

A reasonable approach to getting sphinx output into github pages from `https://daler.github.io/sphinxdoc-test/includeme.html`
