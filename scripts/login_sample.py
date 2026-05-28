import tkinter as tk
from tkinter import messagebox, Menu, ttk
import requests
import hashlib
import os
import sys
import platform
import subprocess
import threading
import time
import datetime
import uuid

# ===================== V7.2 核心配置 =====================
SERVER_URL = "https://www.keyt.cn/kami/qq1444236498/check.php"  # 卡密验证接口，用户名记得改你自己的账号
APP_NAME = "aitui"  # 应用名称，记得改你自己的
SIGN_KEY = "2026@ltj"  # 签名密钥，记得改你自己的
TIMESTAMP_MAX_DIFF = 120  # 时间戳最大容差（秒）
CARD_PATH = r"C:\Windows\Temp\save_card.txt"  # 恢复为原版的明文保存路径

# ===================== 后台验证配置 =====================
LICENSE_CHECK_INTERVAL = 180  # 登陆后持续验证间隔（秒），新版建议50-59秒防封
RETRY_CHECK_INTERVAL = 20  # 当一次验证失败后，每隔多少秒重新验证一次
MAX_RETRY_FAILED = 5  # 连续失败多少次后退出程序
NETWORK_RETRY_MIN_COUNT = 2  # 网络不可达时，至少连续失败次数达到该值才进一步判定
NETWORK_RETRY_MIN_DURATION = 30  # 网络不可达时，累计持续时长达到该值（秒）才进一步判定
LICENSE_INPUT_MAX_LEN = 30  # 卡密最大输入长度

# 全局变量（用于存储卡密到期时间）
REMAINING_TIME = "长期"

# ===================== 界面主题配置 =====================
BG_COLOR = "#F5F7FB"
CARD_COLOR = "#FFFFFF"
PRIMARY = "#2563EB"
PRIMARY_HOVER = "#1D4ED8"
PRIMARY_PRESS = "#1E40AF"
TEXT_MAIN = "#111827"
TEXT_SUB = "#6B7280"
BORDER = "#E5E7EB"
WHITE = "#FFFFFF"


# ===================== 窗口样式辅助 =====================
def center_window(window: tk.Tk, width: int, height: int) -> None:
    """将窗口居中显示。"""
    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = max((screen_width - width) // 2, 0)
    y = max((screen_height - height) // 2, 0)
    window.geometry(f"{width}x{height}+{x}+{y}")


def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except:
        try:
            style.theme_use("clam")
        except:
            pass

    style.configure(".", font=("Microsoft YaHei", 10))
    style.configure("Card.TFrame", background=CARD_COLOR)
    style.configure("Page.TFrame", background=BG_COLOR)
    style.configure("Title.TLabel", background=CARD_COLOR, foreground=TEXT_MAIN, font=("Microsoft YaHei", 18, "bold"))
    style.configure("Sub.TLabel", background=CARD_COLOR, foreground=TEXT_SUB, font=("Microsoft YaHei", 10))
    style.configure("Body.TLabel", background=CARD_COLOR, foreground=TEXT_MAIN, font=("Microsoft YaHei", 10, "bold"))
    style.configure("Hint.TLabel", background=CARD_COLOR, foreground=TEXT_SUB, font=("Microsoft YaHei", 9))
    root.configure(bg=BG_COLOR)


def create_card(parent) -> ttk.Frame:
    return ttk.Frame(parent, style="Card.TFrame")


# ===================== 本地保存逻辑 (完全回退旧版) =====================
def ensure_directory_exists(file_path: str) -> None:
    dir_path = os.path.dirname(file_path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)


def get_hardware_id() -> str:
    """生成唯一硬件标识（作为新版的 MAC 参数使用）。"""
    try:
        node_name = platform.node()
        if platform.system() == "Windows":
            cpu_id = ""
            try:
                cpu_output = subprocess.check_output("wmic cpu get ProcessorId", shell=True, stderr=subprocess.DEVNULL,
                                                     stdin=subprocess.DEVNULL).decode(errors="ignore").split("\n")
                cpu_id = next((line.strip() for line in cpu_output if line.strip() and "ProcessorId" not in line), "")
            except:
                pass
            base_str = (cpu_id + node_name).encode()
        else:
            mac_address = hex(uuid.getnode())
            base_str = (node_name + mac_address).encode()
        return hashlib.md5(base_str).hexdigest()[:16].upper() + "MAC"
    except:
        return "HWID" + os.urandom(4).hex().upper() + "MAC"


def save_license_key(key: str) -> None:
    """原版明文直接存储模式"""
    ensure_directory_exists(CARD_PATH)
    try:
        with open(CARD_PATH, "w", encoding="utf-8") as f:
            f.write(key)
    except:
        pass


def load_license_key() -> str:
    """原版明文读取"""
    if not os.path.isfile(CARD_PATH): return ""
    try:
        with open(CARD_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return ""


def bring_tk_window_to_front() -> None:
    if platform.system() != "Windows": return
    try:
        import win32gui, win32con
        tk_windows = []
        win32gui.EnumWindows(lambda hwnd, param: param.append(hwnd) if win32gui.GetClassName(
            hwnd) == "TkTopLevel" and win32gui.IsWindowVisible(hwnd) else None, tk_windows)
        if tk_windows:
            win32gui.ShowWindow(tk_windows[0], win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(tk_windows[0])
    except:
        pass


# ===================== V7.2 核心网络验证协议与旧版心跳规则 =====================
def md5_string(data_str):
    return hashlib.md5(data_str.encode('utf-8')).hexdigest().lower()


def calc_sign(data):
    key_len = len(SIGN_KEY)
    res = ""
    for i in range(len(data)):
        v = (ord(data[i]) + ord(SIGN_KEY[i % key_len])) % 256
        res += f"{v:02x}"
    return res.lower()


def verify_response(raw, xsign2):
    if "|sign=" not in raw: return ""
    body, sign = raw.split("|sign=", 1)
    local_sign = ""
    if xsign2 and len(xsign2) == 32:
        local_sign = md5_string(body + SIGN_KEY)
        if local_sign != xsign2.lower(): return ""
    elif len(sign) == 32:
        local_sign = md5_string(body + SIGN_KEY)
    else:
        local_sign = calc_sign(body)
    if local_sign.lower() != sign.lower() and (not xsign2 or local_sign.lower() != xsign2.lower()): return ""
    if "|" not in body: return ""
    last_pipe = body.rfind("|")
    ts_str = body[last_pipe + 1:]
    if not ts_str.isdigit() or abs(int(time.time()) - int(ts_str)) > TIMESTAMP_MAX_DIFF: return ""
    return body[:last_pipe]


# --- 旧版 Case 规则翻译器与行为逻辑控制器 ---
def trans_msg_and_get_action(code):
    """
    完美继承旧版的逻辑分流器。
    返回结构: (提示文本, 是否属于通过状态)
    """
    mapping = {
        "already_online": ("该卡密已在线（多设备上限可能已满）", False),
        "online_limit_reached": ("在线设备数已满", False),
        "activate": ("激活成功", True),
        "valid": ("验证通过", True),
        "permanent": ("终身有效", True),
        "heartbeat": ("心跳正常", True),
        "bypass": ("验证已关闭", True),
        "unbind_ok": ("解绑成功", True),
        "invalid_card": ("卡密无效", False),
        "expired": ("卡密已过期", False),
        "banned": ("卡密已被禁用", False),
        "device_mismatch": ("设备不匹配", False),
        "missing_params": ("参数不完整", False)
    }
    if code in mapping:
        return mapping[code]
    return (code, False)


def get_card_switch():
    try:
        t_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        url = f"{SERVER_URL}?act=get_switch&app={APP_NAME}&t={t_str}"
        response = requests.get(url, headers={"Cache-Control": "no-cache"}, timeout=5)
        xsign2 = response.headers.get("X-Sign2", "")
        body = response.text.split("|sign=")[0] if "|sign=" in response.text else response.text
        if xsign2 and md5_string(body + SIGN_KEY) == xsign2.lower():
            return "CARD_ON" if "CARD_ON" in body else "CARD_OFF"
        verify_result = verify_response(response.text, xsign2)
        if verify_result:
            return "CARD_ON" if "CARD_ON" in verify_result else "CARD_OFF"
    except:
        pass
    return "CARD_ON"


def get_notice():
    """获取最新公告，展示在输入框上方"""
    try:
        url = f"{SERVER_URL}?act=get_notice&app={APP_NAME}"
        response = requests.get(url, headers={"Cache-Control": "no-cache"}, timeout=5)
        return response.text if response.text else "暂无最新公告"
    except:
        return "未能连接到服务器获取公告"


def check_card_core(card, mac, is_heartbeat=False):
    """V7.2 网络发包整合旧版规则流"""
    global REMAINING_TIME
    try:
        t_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        # 保持心跳和激活用同一个底层发包，区别在于内部处理
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
        if not biz: biz = verify_response(ret, xsign2)
        if not biz: return "error|签名校验失败或时间戳过期"

        if biz.startswith("error|"):
            raw_code = biz.split('|')[1]
            msg, is_ok = trans_msg_and_get_action(raw_code)
            # 如果是旧版规则里允许的特殊“放行/关闭”状态码(如 bypass)
            if is_ok:
                return f"ok|{msg}"
            return f"error|{msg}"

        # 解析通过逻辑及剩余时间
        arr = biz.split("|")
        status_code = arr[0]  # ok
        days = arr[1] if len(arr) == 2 else arr[2]

        # 强制塞入旧版的心跳成功/验证成功响应检查
        if is_heartbeat:
            msg, _ = trans_msg_and_get_action("heartbeat")
        else:
            msg, _ = trans_msg_and_get_action("valid")

        if days.isdigit():
            if len(arr) >= 4:
                total_mins = int(arr[3])
                REMAINING_TIME = f"{total_mins // 1440}天{(total_mins % 1440) // 60}小时{total_mins % 60}分钟"
            else:
                REMAINING_TIME = f"{days}天"
        elif days.lower() == "permanent":
            msg, _ = trans_msg_and_get_action("permanent")
            REMAINING_TIME = msg
        else:
            REMAINING_TIME = days

        return f"ok|{msg}"
    except requests.exceptions.Timeout:
        return "network|验证接口请求超时"
    except requests.exceptions.ConnectionError:
        return "network|网络连接失败，请检查网络"
    except Exception as e:
        return f"error|验证异常: {str(e)}"


def verify_license_key(key: str, hwid: str, is_heartbeat=False) -> dict:
    """旧版逻辑适配层"""
    result = check_card_core(key, hwid, is_heartbeat)
    if result.startswith("ok|"):
        return {"ok": True, "expire": REMAINING_TIME, "msg": result.split("|")[1]}
    elif result.startswith("network|"):
        return {"ok": False, "msg": result.split("|")[1], "error_type": "network"}
    elif result.startswith("error|"):
        return {"ok": False, "msg": result.split("|")[1], "error_type": "api"}
    else:
        return {"ok": False, "msg": "未知错误", "error_type": "unknown"}


# ===================== 旧版心跳包规则流 (换装新版通讯) =====================
def license_background_check(key: str, hwid: str, exit_callback) -> None:
    failed_count = 0
    network_failed_count = 0
    network_fail_start_time = None
    while True:
        time.sleep(LICENSE_CHECK_INTERVAL)
        # 传入 is_heartbeat=True 启用旧版 heartbeat 规则逻辑
        verify_result = verify_license_key(key, hwid, is_heartbeat=True)

        if verify_result.get("ok", False):
            failed_count = 0
            network_failed_count = 0
            network_fail_start_time = None
            continue

        error_type = verify_result.get("error_type", "")
        if error_type in ("network", "http"):
            if network_fail_start_time is None: network_fail_start_time = time.time()
            network_failed_count += 1
            if network_failed_count < NETWORK_RETRY_MIN_COUNT or (
                    time.time() - network_fail_start_time) < NETWORK_RETRY_MIN_DURATION: continue
        else:
            failed_count += 1

        while failed_count < MAX_RETRY_FAILED:
            time.sleep(RETRY_CHECK_INTERVAL)
            retry_result = verify_license_key(key, hwid, is_heartbeat=True)
            if retry_result.get("ok", False):
                failed_count = 0
                network_failed_count = 0
                network_fail_start_time = None
                break
            if retry_result.get("error_type", "") in ("network", "http"):
                network_failed_count += 1
                if network_fail_start_time is None: network_fail_start_time = time.time()
                if network_failed_count >= NETWORK_RETRY_MIN_COUNT and (
                        time.time() - network_fail_start_time) >= NETWORK_RETRY_MIN_DURATION: failed_count += 1
            else:
                failed_count += 1

        if failed_count >= MAX_RETRY_FAILED:
            def show_error_and_exit():
                messagebox.showerror("程序退出",
                                     f"旧版心跳异常中断：\n{verify_result.get('msg', '未知错误')}\n\n程序即将退出")
                os._exit(0)

            if tk._default_root is not None: tk._default_root.after(0, show_error_and_exit)
            break


# ===================== 主程序逻辑 =====================
def run_main_application() -> None:
    #  ===================主程序代码在这里粘贴================

    #  ===============主程序代码在这里结束==========================
    bring_tk_window_to_front()
    root.mainloop()


# ===================== 激活窗口 =====================
def show_license_activate_window() -> None:
    switch_status = get_card_switch()
    if switch_status == "CARD_OFF":
        # 如果触发旧版 bypass 或新版关闭开关，直接免验证放行
        global REMAINING_TIME
        REMAINING_TIME, _ = trans_msg_and_get_action("bypass")
        run_main_application()
        return

    hwid = get_hardware_id()
    saved_license = load_license_key()

    if saved_license:
        temp_root = tk.Tk()
        temp_root.withdraw()
        verify_result = verify_license_key(saved_license, hwid)
        if verify_result.get("ok", False):
            temp_root.destroy()
            threading.Thread(target=license_background_check, args=(saved_license, hwid, sys.exit), daemon=True).start()
            run_main_application()
            return
        temp_root.destroy()

    activate_window = tk.Tk()
    activate_window.title("软件激活")
    activate_window.geometry("460x420")
    center_window(activate_window, 460, 420)
    apply_theme(activate_window)
    activate_window.resizable(False, False)
    activate_window.protocol("WM_DELETE_WINDOW", lambda: sys.exit(0))

    page = ttk.Frame(activate_window, style="Page.TFrame")
    page.pack(fill="both", expand=True, padx=18, pady=18)
    card = create_card(page)
    card.pack(fill="both", expand=True)



    # --- 【位置修正】公告显示区：精准移到卡密输入框正上方 ---
    notice_text = get_notice()
    notice_frame = tk.Frame(card, bg="#FFFBEB", highlightbackground="#FDE68A", highlightthickness=1)
    notice_frame.pack(fill="x", padx=24, pady=(10, 15))

    notice_label = tk.Label(
        notice_frame,
        text=f"📢 公告：{notice_text}",
        justify="left",
        wraplength=350,
        bg="#FFFBEB",
        fg="#B45309",
        font=("Microsoft YaHei", 9)
    )
    notice_label.pack(anchor="w", padx=12, pady=10)

    input_label = ttk.Label(card, text="💳 请输入您的卡密：", style="Body.TLabel")
    input_label.pack(anchor="w", padx=24)

    # --- 【样式美化】带有输入光晕和等宽字体的输入区域 ---
    input_box = tk.Frame(card, bg="#F3F4F6", highlightbackground="#D1D5DB", highlightthickness=1)
    input_box.pack(fill="x", padx=24, pady=(8, 10))

    license_var = tk.StringVar(value=saved_license[:LICENSE_INPUT_MAX_LEN] if saved_license else "")
    license_entry = tk.Entry(
        input_box, textvariable=license_var, font=("Consolas", 12, "bold"),
        relief="flat", bd=0, fg=PRIMARY, bg="#F3F4F6", insertbackground=PRIMARY, show="",
    )
    license_entry.pack(fill="x", padx=12, pady=10)

    def on_entry_focus_in(e):
        input_box.config(highlightbackground=PRIMARY)  # 蓝色高亮光晕
        license_entry.config(bg="#FFFFFF")
        input_box.config(bg="#FFFFFF")

    def on_entry_focus_out(e):
        input_box.config(highlightbackground="#D1D5DB")  # 恢复浅灰边框
        license_entry.config(bg="#F3F4F6")
        input_box.config(bg="#F3F4F6")

    license_entry.bind("<FocusIn>", on_entry_focus_in)
    license_entry.bind("<FocusOut>", on_entry_focus_out)

    updating_license_text = False

    def normalize_license_text(*_args):
        nonlocal updating_license_text
        if updating_license_text: return
        current_text = license_var.get()
        trimmed_text = current_text[:LICENSE_INPUT_MAX_LEN]
        if current_text != trimmed_text:
            updating_license_text = True
            license_var.set(trimmed_text)
            updating_license_text = False

    license_var.trace_add("write", normalize_license_text)

    def paste_license_text(event=None):
        nonlocal updating_license_text
        try:
            clipboard_text = activate_window.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            selection_start = license_entry.index(tk.SEL_FIRST)
            selection_end = license_entry.index(tk.SEL_LAST)
        except tk.TclError:
            selection_start = license_entry.index(tk.INSERT)
            selection_end = selection_start
        current_text = license_var.get()
        available_space = LICENSE_INPUT_MAX_LEN - (len(current_text) - (selection_end - selection_start))
        if available_space <= 0: return "break"
        insert_text = clipboard_text[:available_space]
        new_text = current_text[:selection_start] + insert_text + current_text[selection_end:]
        updating_license_text = True
        license_var.set(new_text[:LICENSE_INPUT_MAX_LEN])
        updating_license_text = False
        license_entry.icursor(min(selection_start + len(insert_text), LICENSE_INPUT_MAX_LEN))
        return "break"

    def on_paste_shortcut(event):
        return paste_license_text(event)

    license_entry.focus_set()

    right_click_menu = Menu(activate_window, tearoff=0)
    right_click_menu.add_command(label="粘贴", command=paste_license_text)
    license_entry.bind("<Button-3>", lambda e: right_click_menu.post(e.x_root, e.y_root))
    license_entry.bind("<<Paste>>", paste_license_text)
    license_entry.bind("<Control-v>", on_paste_shortcut)
    license_entry.bind("<Control-V>", on_paste_shortcut)

    status_label = ttk.Label(card, text="", style="Hint.TLabel")
    status_label.pack(anchor="w", padx=24, pady=(0, 8))

    def set_status(text: str, color: str = TEXT_SUB) -> None:
        status_label.config(text=text, foreground=color)

    def do_license_verify():
        license_key = license_entry.get().strip()[:LICENSE_INPUT_MAX_LEN]
        if not license_key:
            messagebox.showwarning("提示", "请输入卡密后再验证！", parent=activate_window)
            return
        verify_btn.config(state=tk.DISABLED, text="验证中…")
        set_status("正在与服务器通讯进行加密验证，请稍候…", PRIMARY)
        activate_window.update()

        def verify_task():
            result = verify_license_key(license_key, hwid)
            if result.get("ok", False):
                save_license_key(license_key)

                def on_success():
                    messagebox.showinfo("激活成功",
                                        f"{result.get('msg', '验证通过')}！\n剩余时间：{result.get('expire', '永久有效')}",
                                        parent=activate_window)
                    activate_window.destroy()
                    threading.Thread(target=license_background_check, args=(license_key, hwid, sys.exit),
                                     daemon=True).start()
                    run_main_application()

                activate_window.after(0, on_success)
            else:
                def on_failed():
                    set_status(result.get("msg", "验证失败"), "#DC2626")
                    verify_btn.config(state=tk.NORMAL, text="立即验证并激活")

                activate_window.after(0, on_failed)

        threading.Thread(target=verify_task, daemon=True).start()

    license_entry.bind("<Return>", lambda e: do_license_verify())

    btn_wrap = ttk.Frame(card, style="Card.TFrame")
    btn_wrap.pack(fill="x", padx=24, pady=(5, 0))

    verify_btn = tk.Button(
        btn_wrap, text="立即验证并激活", command=do_license_verify, bg=PRIMARY, fg=WHITE,
        activebackground=PRIMARY_HOVER, activeforeground=WHITE, relief="flat", bd=0,
        cursor="hand2", font=("Microsoft YaHei", 11, "bold"), padx=16, pady=10, highlightthickness=0
    )
    verify_btn.pack(fill="x")

    def on_btn_enter(_event):
        verify_btn.config(bg=PRIMARY_HOVER)

    def on_btn_leave(_event):
        verify_btn.config(bg=PRIMARY if verify_btn['state'] == tk.NORMAL else "#93C5FD")

    def on_btn_press(_event):
        if verify_btn['state'] == tk.NORMAL: verify_btn.config(bg=PRIMARY_PRESS)

    verify_btn.bind("<Enter>", on_btn_enter)
    verify_btn.bind("<Leave>", on_btn_leave)
    verify_btn.bind("<ButtonPress-1>", on_btn_press)

    tip = ttk.Label(card, text="激活后会自动保存在本机，下次启动免输入。", style="Hint.TLabel")
    tip.pack(anchor="center", padx=24, pady=(15, 10))

    bring_tk_window_to_front()
    activate_window.mainloop()


# ===================== 程序入口 =====================
if __name__ == "__main__":
    try:
        show_license_activate_window()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        messagebox.showerror("程序异常", f"程序启动失败：{str(e)}")
        sys.exit(1)
