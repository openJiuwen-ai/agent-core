#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, Tuple


def generate_idx_name(usr_id: str, group_id: str, agent_id: Optional[str] = None, mem_type: Optional[str] = None):
    """generate vector idx name"""
    if agent_id:
        if mem_type:
            return 'agent^{}^{}^{}^{}'.format(usr_id, group_id, agent_id, mem_type)
        else:
            return 'agent^{}^{}^{}^null'.format(usr_id, group_id, agent_id)
    if mem_type:
        return 'agent^{}^{}^null^{}'.format(usr_id, group_id, mem_type)
    return 'agent^{}^{}^null^null'.format(usr_id, group_id)


def parse_memory_hit_infos(hits: list[Tuple[str, float]]) -> tuple[list[str], dict[str, float]]:
    try:
        ids = [hit[0] for hit in hits] if hits else []
        scores = {hit[0]: hit[1] for hit in hits} if hits else {}
        return ids, scores
    except Exception as e:
        raise ValueError(f"Failed to parse memory hit infos: {e}")
