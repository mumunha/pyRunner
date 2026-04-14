"""Supervisord XML-RPC client wrapper."""
from __future__ import annotations

import logging
import xmlrpc.client
from typing import Any

logger = logging.getLogger(__name__)

SUPERVISOR_STATES = {
    2: "FATAL",
    1: "RUNNING",
    0: "RESTARTING",
    -1: "SHUTDOWN",
}

PROCESS_STATES = {
    0: "STOPPED",
    10: "STARTING",
    20: "RUNNING",
    30: "BACKOFF",
    40: "STOPPING",
    100: "EXITED",
    200: "FATAL",
    1000: "UNKNOWN",
}


class SupervisorClient:
    def __init__(self, cfg: dict):
        url = cfg["supervisor"]["xmlrpc_url"]
        username = cfg["supervisor"].get("username", "")
        password = cfg["supervisor"].get("password", "")
        if username and password:
            # Embed credentials in URL
            proto, rest = url.split("://", 1)
            url = f"{proto}://{username}:{password}@{rest}"
        self._url = url
        self._server = xmlrpc.client.ServerProxy(url)

    def _rpc(self, *args, **kwargs):
        """Call supervisor XML-RPC, return None on connection failure."""
        raise NotImplementedError

    def get_state(self) -> dict:
        state = self._server.supervisor.getState()
        return {
            "statecode": state.get("statecode", -1),
            "statename": state.get("statename", "UNKNOWN"),
        }

    def get_all_process_info(self) -> list[dict]:
        try:
            return self._server.supervisor.getAllProcessInfo()
        except Exception as e:
            logger.warning("Supervisor getAllProcessInfo failed: %s", e)
            return []

    def get_process_info(self, name: str) -> dict | None:
        try:
            return self._server.supervisor.getProcessInfo(name)
        except Exception as e:
            logger.warning("Supervisor getProcessInfo(%s) failed: %s", name, e)
            return None

    def start_process(self, name: str, wait: bool = True) -> bool:
        try:
            self._server.supervisor.startProcess(name, wait)
            return True
        except xmlrpc.client.Fault as e:
            logger.warning("Supervisor startProcess(%s) fault: %s", name, e.faultString)
            return False
        except Exception as e:
            logger.warning("Supervisor startProcess(%s) error: %s", name, e)
            return False

    def stop_process(self, name: str, wait: bool = True) -> bool:
        try:
            self._server.supervisor.stopProcess(name, wait)
            return True
        except xmlrpc.client.Fault as e:
            logger.warning("Supervisor stopProcess(%s) fault: %s", name, e.faultString)
            return False
        except Exception as e:
            logger.warning("Supervisor stopProcess(%s) error: %s", name, e)
            return False

    def restart_process(self, name: str) -> bool:
        self.stop_process(name)
        return self.start_process(name)

    def reread(self) -> bool:
        try:
            self._server.supervisor.reloadConfig()
            return True
        except Exception as e:
            logger.warning("Supervisor reloadConfig failed: %s", e)
            return False

    def add_process_group(self, name: str) -> bool:
        try:
            self._server.supervisor.addProcessGroup(name)
            return True
        except xmlrpc.client.Fault as e:
            if "already exists" in e.faultString.lower():
                return True
            logger.warning("Supervisor addProcessGroup(%s) fault: %s", name, e.faultString)
            return False
        except Exception as e:
            logger.warning("Supervisor addProcessGroup(%s) error: %s", name, e)
            return False

    def remove_process_group(self, name: str) -> bool:
        try:
            self._server.supervisor.removeProcessGroup(name)
            return True
        except Exception as e:
            logger.warning("Supervisor removeProcessGroup(%s) error: %s", name, e)
            return False

    def tail_stdout(self, name: str, offset: int = 0, length: int = 4096) -> tuple[str, int, bool]:
        """Returns (log_data, new_offset, overflow)."""
        try:
            result = self._server.supervisor.tailProcessStdoutLog(name, offset, length)
            return result[0], result[1], result[2]
        except Exception as e:
            logger.warning("Supervisor tailProcessStdoutLog(%s) error: %s", name, e)
            return "", offset, False

    def process_state_name(self, statecode: int) -> str:
        return PROCESS_STATES.get(statecode, "UNKNOWN")
