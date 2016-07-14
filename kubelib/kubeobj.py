
class KubeObj(object):
    # attributes that can only be set at object creation time
    create_attributes = []
    # attributes that can be updated
    update_attributes = []
    # attributes set by the server that cannot be changed
    readonly_attributes = []

    def to_json(self):
        out_json = {}

        for attr in self.create_attributes + self.update_attributes + self.readonly_attributes:
            if getattr(self, attr, None) is not None:
                if hasattr(getattr(self, attr), 'to_json'):
                    out_json[attr] = getattr(self, attr).to_json()
                else:
                    out_json[attr] = getattr(self, attr)

        return json.dumps(out_json)

    def __init__(self, **kwargs):
        for arg in kwargs:
            setattr(self, arg, kwargs[arg])

class Pod(KubeObj):
    create_attributes = ['kind', ]
    update_attributes = ['apiVersion', 'metadata', 'spec', ]
    readonly_attributes = ['status',]

class ObjectMeta(KubeObj):
    create_attributes = ['name', 'generateName', 'namespace', 'labels', 'annotations']
    readonly_attributes = [
        'selfLink', 'uid', 'resourceVersion', 'generation', 'creationTimestamp',
        'deletionTimestamp', 'deletionGracePeriodSeconds'
    ]

    def __init__(self, name=None, generateName=None, namespace=None, labels=None, annotations=None):
        self.name = name
        self.generateName = generateName
        self.namespace = namespace
        self.labels = labels
        self.annotations = annotations

        # read only attributes
        for attr in self.readonly_attributes:
            setattr(self, attr, None)

class PodSpec(KubeObj):
    create_attributes = [
        'volumes', 'containers', 'restartPolicy', 'terminationGracePeriodSeconds',
        'activeDeadlineSeconds', 'dnsPolicy', 'nodeSelector', 'serviceAccountName',
        'nodeName', 'hostNetwork', 'hostPID', 'hostIPC',
        'securityContext', 'imagePullSecrets']

    def __init__(self, containers, **kwargs):
        self.containers = containers
        for arg in kwargs:
            setattr(self, arg, kwargs[arg])

class PodStatus(KubeObj):
    readonly_attributes = ['phase', 'conditions', 'message', 'reason', 'hostIP', 'podIP', 'startTime', 'containerStatuses']

class Volume(KubeObj):
    create_attributes = [
        'name', 'hostPath', 'emptyDir', 'gcePersistentDisk', 'awsElasticBlockStore', 'gitRepo', 'secret',
        'nfs', 'iscsi', 'glusterfs', 'persistentVolumeClaim', 'rbd', 'cinder', 'cephfs', 'flocker',
        'downwardAPI', 'fc'
    ]

class Container(KubeObj):
    create_attributes = [
        'name', 'image', 'command', 'args', 'workingdir', 'ports', 'env', 'resources', 'volumeMounts',
        'livenessProbe', 'readinessProbe', 'lifecycle', 'terminationMessagePath', 'imagePullPolicy',
        'securityContext', 'stdin', 'stdinOnce', 'tty'
    ]

class PodSecurityContext(KubeObj):
    create_attributes = [
        'seLinuxOptions', 'runAsUser', 'runAsNonRoot', 'supplementalGroups', 'fsGroup'
    ]

class LocalObjectReference(KubeObj):
    create_attributes = ['name',]

class PodCondition(KubeObj):
    create_attributes = ['type', 'status', 'lastProbeTime', 'lastTransitionTime', 'reason', 'message']

class ContainerStatus(KubeObj):
    create_attributes = ['name', 'state', 'lastState', 'ready', 'restartCount', 'image', 'imageID', 'containerID']

class HostPathVolumeSource(KubeObj):
    create_attributes = ['path', ]

class EmptyDirVolumeSource(KubeObj):
    create_attributes = ['medium', ]
