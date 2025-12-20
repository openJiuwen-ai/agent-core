#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import re
import socket
from struct import unpack
from socket import inet_aton
from urllib.parse import urlparse

from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.utils.common.verify_utils import ExceptionUtils


class UrlUtils:
    @staticmethod
    def check_url_is_valid(url):
        """check url is valid"""
        if not url:
            ExceptionUtils.raise_exception(StatusCode.URL_INVALID_ERROR, 'url is empty')
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname
        if not re.match(r"^https?://.*$", url):
            ExceptionUtils.raise_exception(StatusCode.URL_INVALID_ERROR, 'illegal url protocol')
        try:
            ip_address = socket.gethostbyname(hostname)
        except socket.error:
            ExceptionUtils.raise_exception(StatusCode.URL_INVALID_ERROR, f"resolving IP address failed")
        if UrlUtils._is_inner_ipaddress(ip_address):
            ExceptionUtils.raise_exception(StatusCode.URL_INVALID_ERROR, f"illegal ip address")


    @staticmethod
    def _is_inner_ipaddress(ip):
        """judge inner ip"""
        if os.getenv("SSRF_PROTECT_ENABLED", "true").lower() == "false":
            # only if set SSRF_PROTECT_ENABLED to false, then allow inner ip
            return False

        ip_long = UrlUtils._ip_to_long(ip)
        is_inner_ip = UrlUtils._ip_to_long("10.0.0.0") <= ip_long <= UrlUtils._ip_to_long("10.255.255.255") or \
                      UrlUtils._ip_to_long("172.16.0.0") <= ip_long <= UrlUtils._ip_to_long("172.31.255.255") or \
                      UrlUtils._ip_to_long("192.168.0.0") <= ip_long <= UrlUtils._ip_to_long("192.168.255.255") or \
                      UrlUtils._ip_to_long("127.0.0.0") <= ip_long <= UrlUtils._ip_to_long("127.255.255.255") or \
                      ip_long == UrlUtils._ip_to_long("0.0.0.0")
        return is_inner_ip

    @staticmethod
    def _ip_to_long(ip_addr):
        """ trans ip to long"""
        return unpack("!L", inet_aton(ip_addr))[0]
