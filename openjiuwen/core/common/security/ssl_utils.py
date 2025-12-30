# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import ssl
import stat

from requests.adapters import HTTPAdapter

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.security.exception_utils import ExceptionUtils


class SslUtils:
    @staticmethod
    def create_ssl_adapter(verify_switch_env: str, ssl_cert_env: str, trigger_value: list):
        """create ssl adapter"""
        ssl_verify, ssl_cert = SslUtils.get_ssl_config(verify_switch_env, ssl_cert_env, trigger_value)
        if ssl_verify:
            class SSLAdapter(HTTPAdapter):
                def __init__(self, ssl_context, *args, **kwargs):
                    self.ssl_context = ssl_context
                    super().__init__(*args, **kwargs)

                def init_poolmanager(self, *args, **kwargs):
                    kwargs["ssl_context"] = self.ssl_context
                    return super().init_poolmanager(*args, **kwargs)

            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            adapter = SSLAdapter(ssl_context)
            return adapter
        return None

    @staticmethod
    def get_ssl_config(verify_switch_env: str, ssl_cert_env: str, trigger_value: list, url_is_https: bool = True):
        """get ssl config"""
        if not url_is_https:
            return False, False

        is_ssl_verify_off = SslUtils._bool_env(verify_switch_env, trigger_value)
        ssl_cert = os.getenv(ssl_cert_env)

        if is_ssl_verify_off:
            return False, False

        if ssl_cert is None:
            raise JiuWenBaseException(StatusCode.INVALID_SSL_CERT_ERROR.code,
                                      f"when {verify_switch_env}=true, must provide ssl cert {ssl_cert_env}")

        return True, ssl_cert

    @staticmethod
    def create_strict_ssl_context(ssl_cert: str = None) -> ssl.SSLContext:
        """create strict ssl context"""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3
        ctx.options |= ssl.OP_NO_RENEGOTIATION

        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        ctx.set_ciphers(
            "ECDHE-ECDSA-AES256-GCM-SHA384:"
            "ECDHE-RSA-AES256-GCM-SHA384:"
            "ECDHE-ECDSA-AES128-GCM-SHA256:"
            "ECDHE-RSA-AES128-GCM-SHA256"
        )

        if ssl_cert:
            if os.path.isfile(ssl_cert):
                real_cert_path = os.path.realpath(ssl_cert)

                safe_cert_dir = os.getenv("SAFE_CERT_DIR")
                if safe_cert_dir:
                    safe_prefix = os.path.realpath(safe_cert_dir)
                    if not real_cert_path.startswith(safe_prefix + os.sep):
                        ExceptionUtils.raise_exception(
                            StatusCode.SSL_UTILS_CREATE_SSL_CONTEXT_ERROR,
                            "Certificate path is outside the allowed directory."
                        )
                else:
                    ExceptionUtils.raise_exception(
                        StatusCode.SSL_UTILS_CREATE_SSL_CONTEXT_ERROR,
                        f"SAFE_CERT_DIR is not set.")

                SslUtils._secure_load_cert(ctx, real_cert_path)

        return ctx

    @staticmethod
    def _bool_env(name: str, trigger_value: list) -> bool:
        """parse boolean env"""
        return os.getenv(name, "").strip().lower() in trigger_value

    @staticmethod
    def _secure_load_cert(ctx, ssl_cert):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        mode = stat.S_IRUSR
        try:
            fd = os.open(ssl_cert, flags, mode)
        except OSError:
            ExceptionUtils.raise_exception(
                StatusCode.SSL_UTILS_CREATE_SSL_CONTEXT_ERROR, "Failed to open certificate file")

        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                ExceptionUtils.raise_exception(
                    StatusCode.SSL_UTILS_CREATE_SSL_CONTEXT_ERROR, "file path is invalid")
            if st.st_size == 0 or st.st_size > 1024 * 1024:
                ExceptionUtils.raise_exception(
                    StatusCode.SSL_UTILS_CREATE_SSL_CONTEXT_ERROR, "file size is invalid")

            with os.fdopen(fd, "rb") as f:
                ca_pem = f.read()
            if not ca_pem:
                ExceptionUtils.raise_exception(
                    StatusCode.SSL_UTILS_CREATE_SSL_CONTEXT_ERROR, "file content is empty")
        except Exception:
            os.close(fd)
            ExceptionUtils.raise_exception(
                StatusCode.SSL_UTILS_CREATE_SSL_CONTEXT_ERROR,
                "Failed to read certificate file."
            )

        ctx.load_verify_locations(cadata=ca_pem.decode("ascii"))
