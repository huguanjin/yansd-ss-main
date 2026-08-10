import json
import os
import secrets
import hashlib
import functools
import time
import threading
import urllib.parse
from datetime import datetime

import yaml
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, Response
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from manager import SSManager

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.urandom(32)

CONFIG_PATH = "/data/config.json"
SS_MANAGER_PORT = int(os.environ.get("SS_MANAGER_PORT", "6001"))
SS_METHOD = os.environ.get("SS_METHOD", "chacha20-ietf-poly1305")
DEFAULT_SUBFILE_PATH = os.path.join(os.path.dirname(__file__), "subfile.yaml")

ss = SSManager(port=SS_MANAGER_PORT)

# ─── 配置管理 ────────────────────────────────────────────

_config_cache = None
_mongo_client = None
_mongo_db = None

def load_config():
    """从 /data/config.json 加载配置"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        _config_cache = json.load(f)
    return _config_cache

def save_config(cfg):
    """保存配置到 /data/config.json"""
    global _config_cache
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _config_cache = cfg

def is_configured():
    """是否已完成初始设置"""
    return load_config() is not None

def hash_password(password, salt=None):
    """使用 SHA-256 + salt 哈希密码"""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return salt, hashed

def verify_password(password, salt, hashed):
    """验证密码"""
    return secrets.compare_digest(
        hashlib.sha256((salt + password).encode()).hexdigest(),
        hashed
    )

# ─── MongoDB ─────────────────────────────────────────────

def get_db():
    """获取 MongoDB 数据库实例"""
    global _mongo_client, _mongo_db
    if _mongo_db is not None:
        return _mongo_db
    cfg = load_config()
    if not cfg:
        raise RuntimeError("未配置数据库")
    _mongo_client = MongoClient(cfg["mongo_uri"], serverSelectionTimeoutMS=5000)
    db_name = cfg.get("db_name", "yansd-ss")
    _mongo_db = _mongo_client[db_name]
    return _mongo_db

def init_db():
    """确保 MongoDB 集合和索引存在，初始化订阅文件"""
    db = get_db()
    db.ports.create_index("port", unique=True)
    db.ports.create_index("password")
    # 如果数据库中没有订阅文件，从默认模板导入
    if not db.subfile.find_one({"_id": "current"}):
        try:
            with open(DEFAULT_SUBFILE_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            content = ""
        db.subfile.replace_one(
            {"_id": "current"},
            {"_id": "current", "content": content},
            upsert=True
        )

def _get_subfile_content():
    """从数据库获取订阅文件内容"""
    db = get_db()
    doc = db.subfile.find_one({"_id": "current"})
    return doc["content"] if doc else ""

def _save_subfile_content(content):
    """保存订阅文件内容到数据库"""
    db = get_db()
    db.subfile.replace_one(
        {"_id": "current"},
        {"_id": "current", "content": content},
        upsert=True
    )

def _backup_subfile():
    """将当前订阅文件备份到数据库"""
    db = get_db()
    doc = db.subfile.find_one({"_id": "current"})
    if doc:
        db.subfile.replace_one(
            {"_id": "backup"},
            {"_id": "backup", "content": doc["content"]},
            upsert=True
        )

def is_expired(expires_at):
    """判断端口是否已过期"""
    if not expires_at:
        return False
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%d")
        return datetime.now() >= exp.replace(hour=23, minute=59, second=59)
    except ValueError:
        return False

def sync_ports_to_manager():
    """启动时把数据库中未过期的端口注册到 ss-manager，过期的自动移除"""
    db = get_db()
    for row in db.ports.find():
        try:
            if is_expired(row.get("expires_at")):
                ss.remove(row["port"])
            else:
                ss.add(row["port"], row["password"])
        except Exception:
            pass

def expiry_checker():
    """后台定时检查过期端口，每 60 秒一次"""
    while True:
        time.sleep(60)
        try:
            if not is_configured():
                continue
            db = get_db()
            for row in db.ports.find({"expires_at": {"$ne": None}}):
                if is_expired(row.get("expires_at")):
                    try:
                        ss.remove(row["port"])
                    except Exception:
                        pass
        except Exception:
            pass

# ─── 鉴权 ──────────────────────────────────────────────

def setup_required(f):
    """确保已完成初始设置"""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not is_configured():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "未完成初始设置"}), 503
            return redirect(url_for("setup_page"))
        return f(*args, **kwargs)
    return wrapper

def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not is_configured():
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "未完成初始设置"}), 503
            return redirect(url_for("setup_page"))
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "未登录"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper

# ─── 初始设置 ─────────────────────────────────────────

@app.route("/setup", methods=["GET"])
def setup_page():
    if is_configured():
        return redirect(url_for("login_page"))
    return render_template("setup.html")

def perform_setup(password, mongo_uri, db_name):
    """执行初始设置：校验参数、连接 MongoDB、写入配置和管理员密码。返回 (ok, error_msg)"""
    global _config_cache, _mongo_client, _mongo_db
    mongo_uri = (mongo_uri or "").strip()
    db_name = (db_name or "").strip() or "yansd-ss"

    if not password or len(password) < 4:
        return False, "密码至少 4 个字符"
    if not mongo_uri:
        return False, "请填写 MongoDB 连接字符串"

    # 验证 MongoDB 连接
    try:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        client.close()
    except Exception as e:
        return False, f"MongoDB 连接失败: {e}"

    # 先在内存中临时生效配置，供 init_db/get_db 使用，此时尚未写入磁盘，
    # 避免 init_db 失败时留下半配置状态（config.json 已存在但连接串无效）
    _config_cache = {"mongo_uri": mongo_uri, "db_name": db_name}
    _mongo_client = None
    _mongo_db = None

    try:
        init_db()
        salt, hashed = hash_password(password)
        db = get_db()
        db.config.replace_one(
            {"_id": "admin"},
            {"_id": "admin", "password_salt": salt, "password_hash": hashed},
            upsert=True
        )
    except Exception as e:
        # 回滚内存配置，确保 is_configured() 不会误判为已完成设置
        _config_cache = None
        if _mongo_client is not None:
            _mongo_client.close()
        _mongo_client = None
        _mongo_db = None
        return False, f"数据库初始化失败: {e}"

    # 全部成功后才持久化到磁盘
    save_config({"mongo_uri": mongo_uri, "db_name": db_name})

    return True, None

def auto_setup_from_env():
    """如果环境变量提供了 MongoDB 连接和管理员密码，则在启动时自动完成初始设置"""
    if is_configured():
        return
    mongo_uri = os.environ.get("MONGO_URI")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    db_name = os.environ.get("DB_NAME", "yansd-ss")

    if not mongo_uri or not admin_password:
        return

    ok, err = perform_setup(admin_password, mongo_uri, db_name)
    if ok:
        print("[INFO] 已通过环境变量自动完成初始设置")
    else:
        print(f"[WARN] 环境变量自动初始设置失败，将回退到手动设置页面: {err}")

@app.route("/api/setup", methods=["POST"])
def api_setup():
    if is_configured():
        return jsonify({"error": "已完成设置，不可重复操作"}), 403

    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    mongo_uri = data.get("mongo_uri", "")
    db_name = data.get("db_name", "")

    ok, err = perform_setup(password, mongo_uri, db_name)
    if not ok:
        status = 500 if err.startswith("数据库初始化失败") else 400
        return jsonify({"error": err}), status

    return jsonify({"ok": True})

# ─── 页面路由 ─────────────────────────────────────────

@app.route("/login", methods=["GET"])
@setup_required
def login_page():
    return render_template("login.html")

@app.route("/api/login", methods=["POST"])
def api_login():
    if not is_configured():
        return jsonify({"error": "未完成初始设置"}), 503
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    db = get_db()
    admin = db.config.find_one({"_id": "admin"})
    if not admin or not verify_password(password, admin["password_salt"], admin["password_hash"]):
        time.sleep(1)
        return jsonify({"error": "密码错误"}), 403
    session["logged_in"] = True
    return jsonify({"ok": True})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/")
@login_required
def index():
    return render_template("index.html", method=SS_METHOD)

# ─── API ──────────────────────────────────────────────

@app.route("/api/ports", methods=["GET"])
@login_required
def list_ports():
    db = get_db()
    rows = list(db.ports.find({}, {"_id": 0}).sort("port", 1))
    result = []
    for r in rows:
        expired = is_expired(r.get("expires_at"))
        result.append({
            "port": r["port"],
            "password": r["password"],
            "remark": r.get("remark", ""),
            "expires_at": r.get("expires_at"),
            "expired": expired,
            "created": r.get("created", ""),
        })
    return jsonify(result)

@app.route("/api/ports", methods=["POST"])
@login_required
def add_port():
    data = request.get_json(silent=True) or {}
    port = data.get("port")
    password = data.get("password", "")
    remark = data.get("remark", "")
    expires_at = data.get("expires_at")

    if not port or not isinstance(port, int) or port < 1024 or port > 65535:
        return jsonify({"error": "端口必须是 1024-65535 的整数"}), 400
    if not password or len(password) < 4:
        return jsonify({"error": "密码至少 4 个字符"}), 400
    if len(password) > 128:
        return jsonify({"error": "密码最长 128 个字符"}), 400
    if len(remark) > 200:
        return jsonify({"error": "备注最长 200 个字符"}), 400
    if expires_at:
        try:
            datetime.strptime(expires_at, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "有效期格式必须为 YYYY-MM-DD"}), 400

    db = get_db()
    if db.ports.find_one({"port": port}):
        return jsonify({"error": "端口已存在"}), 409

    try:
        resp = ss.add(port, password)
    except Exception as e:
        return jsonify({"error": f"ss-manager 通信失败: {e}"}), 500

    db.ports.insert_one({
        "port": port,
        "password": password,
        "remark": remark,
        "expires_at": expires_at,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    return jsonify({"ok": True, "manager_resp": resp}), 201

@app.route("/api/ports/<int:port>", methods=["DELETE"])
@login_required
def delete_port(port):
    db = get_db()
    if not db.ports.find_one({"port": port}):
        return jsonify({"error": "端口不存在"}), 404

    try:
        resp = ss.remove(port)
    except Exception as e:
        return jsonify({"error": f"ss-manager 通信失败: {e}"}), 500

    db.ports.delete_one({"port": port})
    return jsonify({"ok": True, "manager_resp": resp})

@app.route("/api/ports/<int:port>", methods=["PUT"])
@login_required
def update_port(port):
    data = request.get_json(silent=True) or {}
    new_password = data.get("password")
    new_remark = data.get("remark")
    new_expires = data.get("expires_at")

    db = get_db()
    existing = db.ports.find_one({"port": port})
    if not existing:
        return jsonify({"error": "端口不存在"}), 404

    updates = {}

    if new_password:
        if len(new_password) < 4:
            return jsonify({"error": "密码至少 4 个字符"}), 400
        if len(new_password) > 128:
            return jsonify({"error": "密码最长 128 个字符"}), 400
        try:
            ss.remove(port)
            ss.add(port, new_password)
        except Exception as e:
            return jsonify({"error": f"ss-manager 通信失败: {e}"}), 500
        updates["password"] = new_password

    if new_remark is not None:
        if len(new_remark) > 200:
            return jsonify({"error": "备注最长 200 个字符"}), 400
        updates["remark"] = new_remark

    if new_expires is not None:
        if new_expires == "":
            updates["expires_at"] = None
        else:
            try:
                datetime.strptime(new_expires, "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "有效期格式必须为 YYYY-MM-DD"}), 400
            updates["expires_at"] = new_expires
            if is_expired(new_expires):
                try:
                    ss.remove(port)
                except Exception:
                    pass

    if updates:
        db.ports.update_one({"port": port}, {"$set": updates})

    return jsonify({"ok": True})

@app.route("/api/generate-password", methods=["GET"])
@login_required
def generate_password():
    pwd = secrets.token_urlsafe(16)
    return jsonify({"password": pwd})

@app.route("/api/next-port", methods=["GET"])
@login_required
def next_port():
    db = get_db()
    row = db.ports.find_one(sort=[("port", -1)])
    max_port = row["port"] if row else 0
    return jsonify({"port": max(15001, max_port + 1)})

# ─── 订阅接口（无需登录，密码即凭证） ─────────────────

@app.route("/sub/<password>", methods=["GET"])
def subscription(password):
    if not is_configured():
        return "Not configured", 503
    db = get_db()
    row = db.ports.find_one({"password": password})
    if not row:
        return "Not found", 404
    if is_expired(row.get("expires_at")):
        return "Expired", 403

    content = _get_subfile_content()
    if not content:
        return "Config not found", 500

    host = request.host.split(":")[0]
    content = content.replace("ServerHost", host)
    content = content.replace("UserPort", str(row["port"]))
    content = content.replace("SecretKey", row["password"])

    # 从订阅文件中提取代理组名称作为文件名
    filename = "clash"
    try:
        parsed = yaml.safe_load(content)
        if parsed and parsed.get("proxy-groups"):
            filename = parsed["proxy-groups"][0]["name"]
    except Exception:
        pass

    resp = Response(content, mimetype="text/yaml; charset=utf-8")
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
    resp.headers["profile-update-interval"] = "12"
    return resp

# ─── Rules 管理接口 ───────────────────────────────────

@app.route("/api/rules", methods=["GET"])
@login_required
def get_rules():
    try:
        content = _get_subfile_content()
        data = yaml.safe_load(content)
        rules = data.get("rules", []) if data else []
        return jsonify(rules)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/rules", methods=["PUT"])
@login_required
def update_rules():
    rules = request.get_json(silent=True)
    if not isinstance(rules, list):
        return jsonify({"error": "rules 必须是数组"}), 400
    for rule in rules:
        if not isinstance(rule, str) or not rule.strip():
            return jsonify({"error": "每条规则必须是非空字符串"}), 400

    try:
        content = _get_subfile_content()
        rules_idx = content.find("\nrules:")
        if rules_idx == -1:
            if content.startswith("rules:"):
                rules_idx = -1
            else:
                return jsonify({"error": "订阅文件中未找到 rules 段"}), 500

        prefix = content[:rules_idx + 1]
        _backup_subfile()
        rules_text = "rules:\n"
        for rule in rules:
            rules_text += f"    - '{rule}'\n"

        _save_subfile_content(prefix + rules_text)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/rules/raw", methods=["GET"])
@login_required
def get_rules_raw():
    try:
        content = _get_subfile_content()
        rules_idx = content.find("\nrules:")
        if rules_idx != -1:
            raw = content[rules_idx + 1:]
        elif content.startswith("rules:"):
            raw = content
        else:
            raw = "rules:\n"
        return jsonify({"raw": raw})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/rules/raw", methods=["PUT"])
@login_required
def update_rules_raw():
    data = request.get_json(silent=True) or {}
    raw = data.get("raw", "").lstrip()
    if not raw.startswith("rules:"):
        return jsonify({"error": "内容必须以 rules: 开头"}), 400
    try:
        parsed = yaml.safe_load(raw)
        if not isinstance(parsed, dict) or "rules" not in parsed:
            return jsonify({"error": "YAML 格式错误或缺少 rules 字段"}), 400
        if len(parsed) != 1:
            return jsonify({"error": "内容只能包含 rules 字段"}), 400
    except yaml.YAMLError as e:
        return jsonify({"error": f"YAML 解析错误: {e}"}), 400
    try:
        _backup_subfile()
        content = _get_subfile_content()
        rules_idx = content.find("\nrules:")
        if rules_idx != -1:
            prefix = content[:rules_idx + 1]
        elif content.startswith("rules:"):
            prefix = ""
        else:
            return jsonify({"error": "订阅文件中未找到 rules 段"}), 500
        if not raw.endswith('\n'):
            raw += '\n'
        _save_subfile_content(prefix + raw)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/rules/restore", methods=["POST"])
@login_required
def restore_rules():
    db = get_db()
    backup = db.subfile.find_one({"_id": "backup"})
    if not backup:
        return jsonify({"error": "没有可恢复的备份"}), 404
    db.subfile.replace_one(
        {"_id": "current"},
        {"_id": "current", "content": backup["content"]},
        upsert=True
    )
    return jsonify({"ok": True})

# ─── 代理组名称管理 ──────────────────────────────────────

@app.route("/api/proxy-group-name", methods=["GET"])
@login_required
def get_proxy_group_name():
    try:
        content = _get_subfile_content()
        data = yaml.safe_load(content)
        groups = data.get("proxy-groups", []) if data else []
        name = groups[0]["name"] if groups else ""
        return jsonify({"name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/proxy-group-name", methods=["PUT"])
@login_required
def update_proxy_group_name():
    data = request.get_json(silent=True) or {}
    new_name = data.get("name", "").strip()
    if not new_name:
        return jsonify({"error": "名称不能为空"}), 400

    try:
        content = _get_subfile_content()
        parsed = yaml.safe_load(content)
        if not parsed:
            return jsonify({"error": "订阅文件为空"}), 500

        groups = parsed.get("proxy-groups", [])
        if not groups:
            return jsonify({"error": "未找到 proxy-groups"}), 500

        old_name = groups[0]["name"]
        if old_name == new_name:
            return jsonify({"ok": True})

        _backup_subfile()
        # 直接在原文中替换：proxy-groups 中的 name 和 proxies 引用、rules 中的动作名
        content = content.replace(old_name, new_name)
        _save_subfile_content(content)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── 启动 ──────────────────────────────────────────────

if __name__ == "__main__":
    # 等待 ss-manager 启动
    time.sleep(2)

    # 如果提供了环境变量，自动完成初始设置
    auto_setup_from_env()

    # 如果已配置，初始化数据库并同步端口
    if is_configured():
        try:
            init_db()
            sync_ports_to_manager()
        except Exception as e:
            print(f"[WARN] 数据库初始化失败，等待重新设置: {e}")

    # 启动过期检查后台线程
    t = threading.Thread(target=expiry_checker, daemon=True)
    t.start()

    web_port = int(os.environ.get("WEB_PORT", "8080"))
    app.run(host="0.0.0.0", port=web_port, debug=False)
