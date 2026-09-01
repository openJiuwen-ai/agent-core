# Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ScienceDiscovery evolve service (search sidecar).

A thin FastAPI service whose only client is the Node control API. It runs the
search loop and streams NDJSON events back; the API owns governance, budget,
accounting, provenance and every write to disk.

Two boundaries this process must never cross:

* **No model key.** Model calls go through the API's loopback proxy with a
  one-shot run token, so a compromised candidate cannot exfiltrate a key that
  was never here.
* **No persistent business state.** Everything needed to replay, resume or
  audit a run lives in the API's ``data/evolution/runs/<runId>/events.ndjson``.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
