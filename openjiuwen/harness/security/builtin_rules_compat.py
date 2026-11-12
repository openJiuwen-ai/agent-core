# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Frozen copy of core's ``harness/resources/builtin_rules.yaml``.

The legacy core layered-policy engine (see :mod:`openjiuwen.harness.security.tiered_compat`)
shipped its own ten shell rules in a YAML file under
``openjiuwen/harness/resources``.  This module keeps those rules verbatim so the
compatibility engine returns byte-identical decisions.  Do **not** replace them
with :mod:`agent_shield.tool_permission.toolguard.builtin_rules` (a different,
17-rule catalog with different severities/actions).

This file is generated from the YAML; keep values byte-for-byte identical.
"""

from __future__ import annotations

from typing import Any

BUILTIN_RULES: list[dict[str, Any]] = [
    {
        "id": "shell_fs_recursive_or_forced_delete",
        "description": "递归、强制或批量删除关键路径，可能造成不可恢复的数据破坏",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(^|[\\s;&|()])((rm\\s+[^;&|]*(-[A-Za-z]*[rR][A-Za-z]*[fF]|-[A-Za-z]*[fF][A-"
            "Za-z]*[rR]|--recursive|--force)[^;&|]*(/[^;&|\\s]*|\\*|~[^;&|\\s]*|\\$HOME[^;&|\\s]*|"
            "\\.{1,2}(/[^;&|\\s]*)?)(\\s|$))|(find\\s+[^;&|]*\\s-delete\\b)|(shred\\b)|(del\\s+[^;&|]"
            "*/[A-Za-z]*[fqs][A-Za-z]*)|(rd\\s+[^;&|]*/s\\b[^;&|]*/q\\b))"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_disk_partition_or_raw_device_write",
        "description": "格式化、分区或直接写入块设备，可能破坏磁盘和文件系统",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(^|[\\s;&|()])((mkfs(\\.[A-Za-z0-9_]+)?|mke2fs|fdisk|parted|diskpart|format"
            ")\\b|(dd\\b[^;&|]*(\\bof=/dev/|\\\\\\\\\\.\\\\PhysicalDrive))|(>\\s*/dev/(sd[a-z][0-9]*|vd["
            "a-z][0-9]*|xvd[a-z][0-9]*|nvme[0-9]+n[0-9]+(p[0-9]+)?|disk[0-9]+)))"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_download_and_execute",
        "description": "从网络下载内容后直接交给 shell、PowerShell 或动态执行器执行",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(^|[\\s;&|()])(((curl|wget|fetch|ftp)\\b[^;&]*\\|\\s*(bash|sh|zsh|dash|ash|so"
            "urce)\\b)|(iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\\b[^;&|]*\\|\\s*(iex|Invoke-"
            "Expression)\\b|((bash|sh|zsh|pwsh|powershell)\\b[^;&|]*<\\s*<\\s*\\(?\\s*(curl|wget)\\b"
            "))"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_obfuscated_or_dynamic_execution",
        "description": "编码载荷、解码后执行、eval/iex 或内联解释器执行，命令难以审计",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(^|[\\s;&|()])((base64\\s+(-d|--decode)\\b[^;&|]*\\|\\s*(bash|sh|zsh|dash|ash)"
            "\\b)|(certutil\\s+-decode\\b)|(-EncodedCommand\\b|-[Ee]nc\\b)|(\\[Convert\\]::FromBase6"
            "4String\\()|(eval\\s+[`$])|(\\b(iex|Invoke-Expression)\\b)|((python3?|perl|ruby|node"
            ")\\s+(-c|-e)\\b[^;&|]*(socket|subprocess|exec|eval|child_process)))"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_reverse_shell_or_bind_shell",
        "description": "反向 shell、绑定 shell 或可疑网络执行通道",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(/dev/(tcp|udp)/|(^|[\\s;&|()])(nc|ncat)\\b[^;&|]*\\s(-e|--exec)\\s|\\bsocat\\b"
            "[^;&|]*(EXEC:|SYSTEM:|PTY)|\\bbash\\s+-i\\b[^;&|]*/dev/tcp/|\\bpython3?\\b[^;&|]*(soc"
            "ket|pty\\.spawn|subprocess)|\\bperl\\b[^;&|]*Socket)"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_privilege_escalation",
        "description": "通过 sudo、su、doas、pkexec、runas 或管理员启动方式提升权限",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(^|[\\s;&|()])((sudo|doas|pkexec)\\s+|su\\s+(-|root|\\w)|runas\\b|Start-Proces"
            "s\\b[^;&|]*-Verb\\s+RunAs\\b|psexec\\b|schtasks\\b[^;&|]*/ru\\s+SYSTEM\\b)"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_data_exfiltration",
        "description": "通过 HTTP、scp、rsync、ftp、nc 等方式上传本地文件或命令输出",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(^|[\\s;&|()])(((curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|irm)\\b["
            "^;&|]*(--data|--data-binary|--upload-file|-d\\b|-F\\b|-T\\b|POST|PUT))|(scp\\b[^;&|]"
            "+\\s+\\S+:)|(rsync\\b[^;&|]+\\s+\\S+:)|(sftp|ftp)\\b|\\bnc\\b[^;&|]*\\s(443|80|8080|53)\\b"
            ")"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_remote_execution_or_lateral_movement",
        "description": "远程会话、远程进程创建或横向移动命令",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(^|[\\s;&|()])((Invoke-Command|Enter-PSSession|New-PSSession)\\b|winrs\\b|wm"
            "ic\\b[^;&|]*process\\s+call\\s+create|psexec\\b|ssh\\b[^;&|]*\\s\\S+\\s+[\"'][^\"']+[\"'])"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_fork_bomb_or_resource_abuse",
        "description": "fork bomb、解除进程限制或批量终止关键进程",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": (
            "re:(?i)(:\\s*\\(\\s*\\)\\s*\\{\\s*:\\s*\\|\\s*:\\s*&\\s*\\}\\s*;\\s*:|(^|[\\s;&|()])kill\\s+-9\\s+"
            "(-1|1)\\b|(^|[\\s;&|()])ulimit\\s+-u\\s+unlimited\\b)"
        ),
        "severity": "CRITICAL",
    },
    {
        "id": "shell_system_shutdown_or_reboot",
        "description": "关机、重启或切换到关机/重启运行级别，属于明显破坏系统可用性的行为",
        "tools": ["bash", "mcp_exec_command", "create_terminal"],
        "match_type": "command",
        "pattern": ("re:(?i)(^|[\\s;&|()])((shutdown|reboot|halt|poweroff)\\b|(init|telinit)\\s+(0|6)\\b)"),
        "action": "deny",
    },
]

__all__ = ["BUILTIN_RULES"]
