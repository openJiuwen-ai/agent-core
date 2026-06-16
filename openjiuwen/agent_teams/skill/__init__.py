# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill + CLI surface for external agents joining / controlling a team.

``cli.py`` is a non-interactive command-line wrapper over
``ExternalTeamClient`` that branches on the join descriptor's ``scope``: a
``member`` (third-party CLI team member) drives the real teammate team tools
(view_task / claim_task / send_message) + inbox, while an ``operator`` (a
non-member, external team controller) gets the broad control surface. The two
scenarios have separate skill docs — ``SKILL_member.md`` and
``SKILL_operator.md`` — each with its own coordination protocol and command
reference.
"""
