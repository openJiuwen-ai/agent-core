from openjiuwen.core.runner.drunner.remote_client import register_remote_client
from openjiuwen.core.runner.drunner.server_adapter import register_server_adapter


def create_a2a_remote_client(**kwargs):
    from openjiuwen.extensions.a2a.a2a_remote_client import A2ARemoteClient
    return A2ARemoteClient(**kwargs)


def create_a2a_server_adapter(**kwargs):
    from openjiuwen.extensions.a2a.a2a_server_adapter import A2AServerAdapter
    return A2AServerAdapter(**kwargs)


register_remote_client("A2A", create_a2a_remote_client)
register_server_adapter("A2A", create_a2a_server_adapter)


# Alternatively, register the plugin via pyproject entry points:
# [project.entry-points."openjiuwen.remote_clients"]
# A2A = "openjiuwen.extensions.a2a.a2a_remote_client:A2ARemoteClient"
# [project.entry-points."openjiuwen.server_adapters"]
# A2A = "openjiuwen.extensions.a2a.a2a_server_adapter:A2AServerAdapter"
