#!/usr/bin/python

import kubelib

k = kubelib.Kubectl()
#print repr(k.base_resources)

#print repr(k.get_pod("www-controller-hi66l"))

#print repr(k.get_namespaces())
#print ("\n\n")
#print repr(k.create_namespace("jkane-test"))
#print ("\n\n")
#print repr(k.delete_namespace("jkane-test"))
#print ("\n\n")
for ns in k.get_namespaces():
	print ns.metadata.name