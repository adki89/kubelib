Kubelib
*******

kubelib both wraps kubectl and make direct kubernetes api calls.  It is intended to simplify scripting jenkins tasks.  It is purely client side; nothing needs to be installed on the node or pods.

This module makes use of the "bunch" python module for object return values.

https://github.com/dsc/bunch

tldr: you can use them as dictionaries (ala pod['metadata']['name']) or with attributes (ala pod.metadata.name).  I think this makes code with deeply nested dictionaries easier to read.


.. automodule:: kubelib


Kubectl
-------

    .. autoclass:: kubelib.Kubectl
        :member-order: groupwise

KubeConfig
----------

    .. autoclass:: kubelib.KubeConfig
        :member-order: groupwise

KubeUtils
---------

    .. autoclass:: kubelib.KubeUtils
        :member-order: groupwise

Kuberentes
----------

    .. autoclass:: kubelib.Kubernetes
        :member-order: groupwise

Resources
---------

Deployment
^^^^^^^^^^

    .. autoclass:: kubelib.Deployment
        :member-order: groupwise

DaemonSet
^^^^^^^^^

    .. autoclass:: kubelib.DaemonSet
        :member-order: groupwise

Namespace
^^^^^^^^^

    .. autoclass:: kubelib.Namespace
        :member-order: groupwise

Node
^^^^

    .. autoclass:: kubelib.Node
        :member-order: groupwise

PersistentVolume
^^^^^^^^^^^^^^^^

    .. autoclass:: kubelib.PersistentVolume
        :member-order: groupwise

PersistentVolumeClaim
^^^^^^^^^^^^^^^^^^^^^

    .. autoclass:: kubelib.PersistentVolumeClaim
        :member-order: groupwise

Pod
^^^

    .. autoclass:: kubelib.Pod
        :member-order: groupwise

ReplicationController
^^^^^^^^^^^^^^^^^^^^^

    .. autoclass:: kubelib.ReplicationController
        :member-order: groupwise

Service
^^^^^^^

    .. autoclass:: kubelib.Service
        :member-order: groupwise

Secret
^^^^^^

    .. autoclass:: kubelib.Secret
        :member-order: groupwise


Role Based Access Control (RBAC)
--------------------------------

Role
^^^^

    .. autoclass:: kubelib.Role
        :member-order: groupwise

ClusterRole
^^^^^^^^^^^

    .. autoclass:: kubelib.ClusterRole
        :member-order: groupwise

RoleBinding
^^^^^^^^^^^

    .. autoclass:: kubelib.RoleBinding
        :member-order: groupwise

ClusterRoleBinding
^^^^^^^^^^^^^^^^^^

    .. autoclass:: kubelib.ClusterRoleBinding
        :member-order: groupwise
