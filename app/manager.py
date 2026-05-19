"""
ss-manager UDP 通信模块
通过 UDP 向 ss-manager 发送指令来管理端口
"""

import json
import socket


class SSManager:
    def __init__(self, host: str = "127.0.0.1", port: int = 6001, timeout: float = 3.0):
        self.addr = (host, port)
        self.timeout = timeout

    def _send(self, cmd: str) -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            s.sendto(cmd.encode(), self.addr)
            data, _ = s.recvfrom(4096)
            return data.decode()

    def add(self, server_port: int, password: str) -> str:
        payload = json.dumps({"server_port": server_port, "password": password})
        return self._send(f"add: {payload}")

    def remove(self, server_port: int) -> str:
        payload = json.dumps({"server_port": server_port})
        return self._send(f"remove: {payload}")

    def ping(self) -> dict:
        """返回当前活跃端口的流量统计 {port: bytes}"""
        try:
            resp = self._send("ping")
            # 响应格式: stat: {"8388": 0, "8389": 123}
            if resp.startswith("stat:"):
                return json.loads(resp[5:].strip())
        except (socket.timeout, OSError):
            pass
        return {}
