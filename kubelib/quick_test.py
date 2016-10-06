#!/usr/bin/python

import kubelib
import logging, logging.config

logging.config.dictConfig({
    'version': 1,
    'formatters': {
        'detailed': {
            'class': 'logging.Formatter',
            'format': '%(asctime)s %(name)-15s %(levelname)-8s %(processName)-10s %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'DEBUG',
            'formatter': 'detailed'
        }
    },
    'loggers': {
        'kubelib': {
            'level': 'DEBUG',
            'propagate': True
        }
    },
    'root': {
        'level': 'DEBUG',
        'handlers': ['console']
    }
})

LOG = logging.getLogger(__name__)

#k = kubelib.Nameubectl()
#print repr(k.base_resources)

#print repr(k.get_pod("www-controller-hi66l"))

#print repr(k.get_namespaces())
#print ("\n\n")
#print repr(k.create_namespace("jkane-test"))
#print ("\n\n")
#print repr(k.delete_namespace("jkane-test"))
#print ("\n\n")
for ns in kubelib.Namespace(kubelib.KubeConfig()).get_list():
	print(ns.metadata.name)
