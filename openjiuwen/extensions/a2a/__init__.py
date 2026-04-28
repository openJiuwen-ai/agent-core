from openjiuwen.core.runner.drunner.remote_client import register_remote_client


def create_a2a_remote_client(**kwargs):
    from openjiuwen.extensions.a2a.a2a_remote_client import A2ARemoteClient
    return A2ARemoteClient(**kwargs)


register_remote_client("A2A", create_a2a_remote_client)


# Alternatively, register the plugin via pyproject entry points:
# [project.entry-points."openjiuwen.remote_clients"]
# A2A = "openjiuwen.extensions.a2a.a2a_remote_client:A2ARemoteClient"
