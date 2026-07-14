# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Auto-launch of external CLI agent backends as team members (P2).

When the team spawns an external CLI-backed member, this package supplies the
backend registry (``backends``), generic per-CLI launch knowledge
(``adapters``), the side-channel input transport (``injector``), and the spawn
entry that wires a backend runtime into the team (``spawn``).

Side-channel injection uses the CLI's **stdin pipe** (Unix-first; the
``Injector`` Protocol leaves room for PTY / Windows backends later). Only
CLIs that read stdin continuously support mid-turn steer; others degrade to
turn-boundary delivery.
"""
