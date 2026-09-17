"""Paper-tree Provider layer — adapts the existing pipeline to the tree-of-
paper-attempts contract in docs/autoresearch_endpoint.md /
docs/autoresearch_module-endpoint.md. ManagerRuntime's optional stage callback
forwards module starts into the current node's durable stage snapshot. See
docs/paper_tree_orchestrator_design.md for the design this implements.
"""
