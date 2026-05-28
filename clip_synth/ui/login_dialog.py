import hashlib
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import uuid

import requests
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clip_synth.utils.ffmpeg_helper import get_resource_path

logger = logging.getLogger("clip_synth.login")

SERVER_URL = "https://www.keyt.cn/kami/qq1444236498/check.php"
APP_NAME = "aitui"
SIGN_KEY = "2026@ltj"
TIMESTAMP_MAX_DIFF = 120
CARD_PATH = r"C:\Windows\Temp\save_card.txt"
LICENSE_CHECK_INTERVAL = 180
RETRY_CHECK_INTERVAL = 20
MAX_RETRY_FAILED = 5
NETWORK_RETRY_MIN_COUNT = 2
NETWORK_RETRY_MIN_DURATION = 30
LICENSE_INPUT_MAX_LEN = 50


def get_hardware_id() -> str:
    try:
        node_name = platform.node()
        if platform.system() == "Windows":
            cpu_id = ""
            try:
                cpu_output = subprocess.check_output(
                    "wmic cpu get ProcessorId", shell=True, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                ).decode(errors="ignore").split("\n")
                cpu_id = next((line.strip() for line in cpu_output if line.strip() and "ProcessorId" not in line), "")
            except Exception:
                pass
            base_str = (cpu_id + node_name).encode()
        else:
            mac_address = hex(uuid.getnode())
            base_str = (node_name + mac_address).encode()
        return hashlib.md5(base_str).hexdigest()[:16].upper() + "MAC"
    except Exception:
        return "HWID" + os.urandom(4).hex().upper() + "MAC"


def save_license_key(key: str) -> None:
    dir_path = os.path.dirname(CARD_PATH)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    try:
        with open(CARD_PATH, "w", encoding="utf-8") as f:
            f.write(key)
    except Exception:
        pass


def load_license_key() -> str:
    if not os.path.isfile(CARD_PATH):
        return ""
    try:
        with open(CARD_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def md5_string(data_str: str) -> str:
    return hashlib.md5(data_str.encode("utf-8")).hexdigest().lower()


def calc_sign(data: str) -> str:
    key_len = len(SIGN_KEY)
    res = ""
    for i in range(len(data)):
        v = (ord(data[i]) + ord(SIGN_KEY[i % key_len])) % 256
        res += f"{v:02x}"
    return res.lower()


def verify_response(raw: str, xsign2: str) -> str:
    if "|sign=" not in raw:
        return ""
    body, sign = raw.split("|sign=", 1)
    if xsign2 and len(xsign2) == 32:
        local_sign = md5_string(body + SIGN_KEY)
        if local_sign != xsign2.lower():
            return ""
    elif len(sign) == 32:
        local_sign = md5_string(body + SIGN_KEY)
    else:
        local_sign = calc_sign(body)
    if local_sign.lower() != sign.lower() and (not xsign2 or local_sign.lower() != xsign2.lower()):
        return ""
    if "|" not in body:
        return ""
    last_pipe = body.rfind("|")
    ts_str = body[last_pipe + 1:]
    if not ts_str.isdigit() or abs(int(time.time()) - int(ts_str)) > TIMESTAMP_MAX_DIFF:
        return ""
    return body[:last_pipe]


def check_card_core(card: str, mac: str) -> dict:
    try:
        t_str = time.strftime("%Y%m%d%H%M%S")
        url = f"{SERVER_URL}?card={card}&mac={mac}&app={APP_NAME}&heart=1&t={t_str}"
        response = requests.get(url, headers={"Cache-Control": "no-cache"}, timeout=10)
        ret = response.text
        xsign2 = response.headers.get("X-Sign2", "")
        body = ret.split("|sign=")[0] if "|sign=" in ret else ret
        biz = ""
        if xsign2 and md5_string(body + SIGN_KEY) == xsign2.lower():
            if "|" in body:
                last_pipe = body.rfind("|")
                ts_str = body[last_pipe + 1:]
                if ts_str.isdigit() and abs(int(time.time()) - int(ts_str)) <= TIMESTAMP_MAX_DIFF:
                    biz = body[:last_pipe]
        if not biz:
            biz = verify_response(ret, xsign2)
        if not biz:
            return {"ok": False, "msg": "签名校验失败或时间戳过期"}

        if biz.startswith("error|"):
            raw_code = biz.split("|")[1]
            return _resolve_code(raw_code)

        arr = biz.split("|")
        days = arr[1] if len(arr) > 1 else "0"
        remaining = ""
        if days.isdigit():
            if len(arr) >= 4 and arr[3].isdigit():
                total_mins = int(arr[3])
                remaining = f"{total_mins // 1440}天{(total_mins % 1440) // 60}小时{total_mins % 60}分钟"
            else:
                remaining = f"{days}天"
        elif days.lower() == "permanent":
            remaining = "终身有效"
        else:
            remaining = days
        return {"ok": True, "remaining": remaining, "msg": "验证通过"}

    except requests.exceptions.Timeout:
        return {"ok": False, "msg": "验证接口请求超时", "network": True}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "msg": "网络连接失败，请检查网络", "network": True}
    except Exception as e:
        return {"ok": False, "msg": f"验证异常: {e}"}


def _resolve_code(code: str) -> dict:
    mapping = {
        "already_online": ("该卡密已在线（多设备上限可能已满）", False),
        "online_limit_reached": ("在线设备数已满", False),
        "invalid_card": ("卡密无效", False),
        "expired": ("卡密已过期", False),
        "banned": ("卡密已被禁用", False),
        "device_mismatch": ("设备不匹配", False),
        "missing_params": ("参数不完整", False),
    }
    msg, ok = mapping.get(code, (code, False))
    return {"ok": ok, "msg": msg}


def get_card_switch() -> str:
    try:
        t_str = time.strftime("%Y%m%d%H%M%S")
        url = f"{SERVER_URL}?act=get_switch&app={APP_NAME}&t={t_str}"
        response = requests.get(url, headers={"Cache-Control": "no-cache"}, timeout=5)
        xsign2 = response.headers.get("X-Sign2", "")
        body = response.text.split("|sign=")[0] if "|sign=" in response.text else response.text
        if xsign2 and md5_string(body + SIGN_KEY) == xsign2.lower():
            return "CARD_ON" if "CARD_ON" in body else "CARD_OFF"
        verify_result = verify_response(response.text, xsign2)
        if verify_result:
            return "CARD_ON" if "CARD_ON" in verify_result else "CARD_OFF"
    except Exception:
        pass
    return "CARD_ON"


def get_notice() -> str:
    try:
        url = f"{SERVER_URL}?act=get_notice&app={APP_NAME}"
        response = requests.get(url, headers={"Cache-Control": "no-cache"}, timeout=5)
        return response.text if response.text else "暂无最新公告"
    except Exception:
        return "未能连接到服务器获取公告"


class VerifyWorker(QThread):
    finished = Signal(dict)

    def __init__(self, license_key: str, hwid: str):
        super().__init__()
        self._license_key = license_key
        self._hwid = hwid

    def run(self) -> None:
        result = check_card_core(self._license_key, self._hwid)
        self.finished.emit(result)


class HeartbeatThread(QThread):
    heartbeat_failed = Signal(str)

    def __init__(self, license_key: str, hwid: str):
        super().__init__()
        self._license_key = license_key
        self._hwid = hwid
        self._running = True

    def run(self) -> None:
        failed_count = 0
        network_failed_count = 0
        network_fail_start_time = None

        while self._running:
            time.sleep(LICENSE_CHECK_INTERVAL)
            result = check_card_core(self._license_key, self._hwid)
            if result.get("ok", False):
                failed_count = 0
                network_failed_count = 0
                network_fail_start_time = None
                continue

            error_type = "network" if result.get("network") else "api"
            if error_type == "network":
                if network_fail_start_time is None:
                    network_fail_start_time = time.time()
                network_failed_count += 1
                if network_failed_count < NETWORK_RETRY_MIN_COUNT or \
                        (time.time() - network_fail_start_time) < NETWORK_RETRY_MIN_DURATION:
                    continue
            else:
                failed_count += 1

            while failed_count < MAX_RETRY_FAILED and self._running:
                time.sleep(RETRY_CHECK_INTERVAL)
                retry_result = check_card_core(self._license_key, self._hwid)
                if retry_result.get("ok", False):
                    failed_count = 0
                    network_failed_count = 0
                    network_fail_start_time = None
                    break
                if retry_result.get("network"):
                    network_failed_count += 1
                    if network_fail_start_time is None:
                        network_fail_start_time = time.time()
                    if network_failed_count >= NETWORK_RETRY_MIN_COUNT and \
                            (time.time() - network_fail_start_time) >= NETWORK_RETRY_MIN_DURATION:
                        failed_count += 1
                else:
                    failed_count += 1

            if failed_count >= MAX_RETRY_FAILED:
                self.heartbeat_failed.emit(result.get("msg", "验证异常中断"))
                break

    def stop(self) -> None:
        self._running = False


class LoginDialog(QDialog):
    _instance: "LoginDialog | None" = None

    @classmethod
    def authenticate(cls, parent: QWidget | None = None) -> bool:
        card_switch = get_card_switch()
        if card_switch == "CARD_OFF":
            logger.info("卡密验证已关闭，直接放行")
            return True

        hwid = get_hardware_id()
        saved_key = load_license_key()
        if saved_key:
            result = check_card_core(saved_key, hwid)
            if result.get("ok", False):
                logger.info("本地保存的卡密验证通过")
                return True

        dialog = cls(parent)
        accepted = dialog.exec() == QDialog.Accepted
        if dialog._heartbeat:
            dialog._heartbeat.stop()
            dialog._heartbeat.wait(1000)
        return accepted

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("软件激活")
        self.setFixedSize(440, 320)
        self.setObjectName("loginDialog")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._heartbeat: HeartbeatThread | None = None
        self._setup_ui()
        self._load_notice()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        title = QLabel("软件激活")
        title.setObjectName("loginTitle")
        layout.addWidget(title)

        subtitle = QLabel("请输入卡密以激活软件")
        subtitle.setObjectName("loginSubtitle")
        layout.addWidget(subtitle)

        self._notice_label = QLabel("")
        self._notice_label.setObjectName("loginNotice")
        self._notice_label.setWordWrap(True)
        self._notice_label.setVisible(False)
        layout.addWidget(self._notice_label)

        self._key_input = QLineEdit()
        self._key_input.setObjectName("loginInput")
        self._key_input.setPlaceholderText("请输入卡密")
        self._key_input.setMaxLength(LICENSE_INPUT_MAX_LEN)
        self._key_input.returnPressed.connect(self._on_verify)
        layout.addWidget(self._key_input)

        self._status_label = QLabel("")
        self._status_label.setObjectName("loginStatus")
        layout.addWidget(self._status_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        self._verify_btn = QPushButton("激活")
        self._verify_btn.setObjectName("loginConfirmBtn")
        self._verify_btn.setCursor(Qt.PointingHandCursor)
        self._verify_btn.clicked.connect(self._on_verify)
        btn_row.addWidget(self._verify_btn)

        layout.addLayout(btn_row)

    def _load_notice(self) -> None:
        def _fetch():
            notice = get_notice()
            if notice and notice != "暂无最新公告":
                self._notice_label.setText(f"📢 {notice}")
                self._notice_label.setVisible(True)
        threading.Thread(target=_fetch, daemon=True).start()

    def _set_status(self, text: str, is_error: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            "color: #f87171;" if is_error else "color: #4ade80;"
        )

    def _on_verify(self) -> None:
        key = self._key_input.text().strip()
        if not key:
            self._set_status("请输入卡密", True)
            return

        self._verify_btn.setEnabled(False)
        self._verify_btn.setText("验证中...")
        self._set_status("正在验证，请稍候...")

        hwid = get_hardware_id()
        self._worker = VerifyWorker(key, hwid)
        self._worker.finished.connect(self._on_verify_result)
        self._worker.start()

    def _on_verify_result(self, result: dict) -> None:
        self._verify_btn.setEnabled(True)
        self._verify_btn.setText("激活")

        if result.get("ok", False):
            remaining = result.get("remaining", "")
            msg = f"激活成功！剩余：{remaining}" if remaining else "激活成功！"
            self._set_status(msg)
            save_license_key(self._key_input.text().strip())
            self._start_heartbeat()
            QTimer.singleShot(800, self.accept)
        else:
            self._set_status(result.get("msg", "验证失败"), True)

    def _start_heartbeat(self) -> None:
        key = self._key_input.text().strip()
        hwid = get_hardware_id()
        self._heartbeat = HeartbeatThread(key, hwid)
        self._heartbeat.heartbeat_failed.connect(self._on_heartbeat_failed)
        self._heartbeat.start()

    def _on_heartbeat_failed(self, msg: str) -> None:
        logger.error("心跳验证失败: %s", msg)
