#!/usr/bin/env python3
"""
Reasonix Assist — 供 Codex 通过 shell 调用的工作区分析工具。
无需 Reasonix 在线，直接读取已生成的知识库和分析成果。

用法: python reasonix_assist.py <命令> [参数...]
"""

import json, os, sys, glob, sqlite3
from datetime import datetime

WORKSPACE = os.path.expandvars(
    r"C:\Users\45543\Downloads\HMCL-3.9.1\.minecraft\versions\3c3u"
)
BRIDGE_DB = os.path.expandvars(
    r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\bridge.db"
)
CODEX_HOME = os.path.expandvars(r"C:\Users\45543\.codex")

def usage():
    print("""
Reasonix Assist — Codex 辅助工具

命令:
  ws-knowledge <section>    查询工作区知识库（section: all|mods|crashes|config|players|world|bridge|issues）
  crash-latest              显示最新崩溃摘要
  crash-list                列出所有崩溃报告
  mod-load-status            显示 MOD 加载状态统计
  config-read <path>         读取配置文件（相对于 3c3u/ 的路径）
  config-search <pattern>    在所有配置文件中搜索关键字
  bridge-status              查看 Agent Bridge 状态
  bridge-knowledge [key]     读 Bridge 共享知识（空=列出全部key）
  skill-list                 列出 Codex 所有可用技能
  session-latest             显示 Codex 最新会话摘要
  deps-tree <modname>        查看 MOD 依赖树（实验性）
  help                       显示此帮助
""")

# ──── 工作区知识库查询 ────

def ws_knowledge(section="all"):
    """Read workspace knowledge from the shared knowledge base"""
    knowledge = {}
    try:
        db = sqlite3.connect(BRIDGE_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT key, value, updated_at FROM knowledge ORDER BY key").fetchall()
        for r in rows:
            knowledge[r["key"]] = r["value"]
        db.close()
    except:
        pass
    
    if section == "all":
        for k, v in knowledge.items():
            print(f"\n{'='*60}")
            print(f"  {k}")
            print(f"{'='*60}")
            print(v[:2000])
    elif section in knowledge:
        print(knowledge[section])
    else:
        print(f"Unknown section: {section}")

# ──── 崩溃分析 ────

def crash_latest():
    """Show latest crash report summary"""
    crash_dir = os.path.join(WORKSPACE, "crash-reports")
    files = sorted(glob.glob(os.path.join(crash_dir, "crash-*.txt")), reverse=True)
    if not files:
        print("No crash reports found.")
        return
    with open(files[0], "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    # Extract key info
    for line in content.split("\n")[:80]:
        if any(k in line.lower() for k in ["error", "exception", "caused by", "failed", "mixin", "classnotfound"]):
            print(line.strip())

def crash_list():
    """List all crash reports with dates and causes"""
    crash_dir = os.path.join(WORKSPACE, "crash-reports")
    files = sorted(glob.glob(os.path.join(crash_dir, "crash-*.txt")))
    print(f"{'Date':<20} {'Size':>8}  {'Root Cause'}")
    print("-" * 70)
    for f in files:
        name = os.path.basename(f)
        date = name.replace("crash-", "").replace("-client.txt", "")
        size = os.path.getsize(f)
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            first_lines = "".join([fh.readline() for _ in range(30)])
        cause = "unknown"
        for line in first_lines.split("\n"):
            if "Caused by:" in line:
                cause = line.split("Caused by:")[-1].strip()[:60]
                break
            if "Exception:" in line:
                cause = line.strip()[:60]
                break
        print(f"{date:<20} {size:>8,}  {cause}")

# ──── MOD 状态 ────

def mod_load_status():
    """Show MOD loading statistics"""
    latest_log = os.path.join(WORKSPACE, "logs", "latest.log")
    mods_loaded = 0
    modlist_started = False
    mod_names = []
    
    try:
        with open(latest_log, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Loading" in line and "mods:" in line:
                    print(line.strip())
                if "mods:" in line and not modlist_started:
                    modlist_started = True
                if modlist_started and line.strip().startswith("- "):
                    mods_loaded += 1
                    mod_names.append(line.strip())
                if modlist_started and not line.strip().startswith("- ") and not line.strip().startswith("|") and not line.strip().startswith("\\"):
                    if mods_loaded > 0:
                        break
    except FileNotFoundError:
        print("latest.log not found")
        return
    
    print(f"\nTotal mods loaded: {mods_loaded}")
    print(f"(Expected 119 client mods — {119 - mods_loaded} failed to load)")

# ──── 配置读取/搜索 ────

def config_read(path):
    """Read a config file"""
    fullpath = os.path.join(WORKSPACE, path)
    if not os.path.exists(fullpath):
        print(f"File not found: {fullpath}")
        return
    with open(fullpath, "r", encoding="utf-8", errors="replace") as f:
        print(f.read()[:5000])

def config_search(pattern):
    """Search for a pattern in all config files"""
    import fnmatch
    config_dir = os.path.join(WORKSPACE, "config")
    results = []
    for root, dirs, files in os.walk(config_dir):
        for fn in files:
            if fnmatch.fnmatch(fn, "*.json") or fnmatch.fnmatch(fn, "*.toml") or fnmatch.fnmatch(fn, "*.properties"):
                fp = os.path.join(root, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if pattern.lower() in line.lower():
                                rel = os.path.relpath(fp, WORKSPACE)
                                results.append(f"{rel}:{i}: {line.strip()[:120]}")
                except:
                    pass
    for r in results[:50]:
        print(r)
    print(f"\n--- {len(results)} matches total ---")

# ──── Bridge 状态 ────

def bridge_status():
    """Show Agent Bridge status"""
    try:
        db = sqlite3.connect(BRIDGE_DB)
        db.row_factory = sqlite3.Row
        
        print("=== Agents ===")
        for r in db.execute("SELECT * FROM agents").fetchall():
            print(f"  {r['name']}: {r['status']} (last heartbeat: {r['last_heartbeat']})")
        
        print("\n=== Pending Tasks ===")
        for r in db.execute("SELECT * FROM tasks WHERE status NOT IN ('done','failed') ORDER BY created_at").fetchall():
            print(f"  [{r['status']}] {r['from_agent']}->{r['to_agent']}: {r['title'][:60]}")
        
        print("\n=== Knowledge Keys ===")
        for r in db.execute("SELECT key, updated_at FROM knowledge ORDER BY key").fetchall():
            print(f"  {r['key']} ({r['updated_at']})")
        
        db.close()
    except Exception as e:
        print(f"Bridge error: {e}")

def bridge_knowledge(key=None):
    """Read bridge knowledge"""
    try:
        db = sqlite3.connect(BRIDGE_DB)
        db.row_factory = sqlite3.Row
        if key:
            r = db.execute("SELECT * FROM knowledge WHERE key=?", (key,)).fetchone()
            if r:
                print(r["value"])
            else:
                print(f"Key not found: {key}")
        else:
            for r in db.execute("SELECT key, updated_at FROM knowledge ORDER BY key").fetchall():
                print(f"  {r['key']} ({r['updated_at']})")
        db.close()
    except Exception as e:
        print(f"Bridge error: {e}")

# ──── Codex 信息 ────

def skill_list():
    """List all Codex skills"""
    skills_dir = os.path.join(CODEX_HOME, "skills")
    for root, dirs, files in os.walk(skills_dir):
        if "SKILL.md" in files:
            skill_path = os.path.join(root, "SKILL.md")
            try:
                with open(skill_path, "r", encoding="utf-8", errors="replace") as f:
                    desc = ""
                    for line in f:
                        if line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
                            break
                rel = os.path.relpath(root, skills_dir)
                print(f"  {rel:<40} {desc[:60]}")
            except:
                pass

def session_latest():
    """Show latest Codex session summary"""
    sessions_dir = os.path.join(CODEX_HOME, "sessions")
    # Find latest session file
    all_sessions = []
    for root, dirs, files in os.walk(sessions_dir):
        for fn in files:
            if fn.endswith(".jsonl"):
                all_sessions.append(os.path.join(root, fn))
    all_sessions.sort(reverse=True)
    if not all_sessions:
        print("No sessions found")
        return
    
    latest = all_sessions[0]
    print(f"Latest session: {os.path.basename(latest)}")
    print(f"Size: {os.path.getsize(latest):,} bytes")
    
    # Extract key events
    with open(latest, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                evt = json.loads(line)
                ts = evt.get("timestamp", "")
                ptype = evt.get("type", "")
                msg = ""
                if ptype == "agent_message":
                    msg = evt.get("payload", {}).get("message", "")[:100]
                    print(f"  [{ts}] {msg}")
                elif ptype == "task_complete":
                    dur = evt.get("payload", {}).get("duration_ms", 0)
                    print(f"  [{ts}] Task completed in {dur}ms")
            except:
                pass

# ──── Main ────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
        sys.exit(0)
    
    cmd = sys.argv[1]
    args = sys.argv[2:]
    
    commands = {
        "ws-knowledge": lambda: ws_knowledge(args[0] if args else "all"),
        "crash-latest": crash_latest,
        "crash-list": crash_list,
        "mod-load-status": mod_load_status,
        "config-read": lambda: config_read(args[0]) if args else print("Need path"),
        "config-search": lambda: config_search(args[0]) if args else print("Need pattern"),
        "bridge-status": bridge_status,
        "bridge-knowledge": lambda: bridge_knowledge(args[0] if args else None),
        "skill-list": skill_list,
        "session-latest": session_latest,
        "help": usage,
    }
    
    if cmd in commands:
        commands[cmd]()
    else:
        print(f"Unknown command: {cmd}")
        usage()
