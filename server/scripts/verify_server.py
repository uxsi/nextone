"""验证 next-edit-server 端到端流程的脚本。

模拟 VS Code 客户端行为，验证两个场景：
1. 同文件 rename 传播（Phase 1）
2. 跨文件 rename 传播（Phase 2）

用法：
    cd server
    pip install -e .
    python scripts/verify_server.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time


def send_message(proc: subprocess.Popen, msg: dict) -> None:
    """向子进程 stdin 发送一条 LSP base protocol 帧格式的 JSON-RPC 消息。"""
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    proc.stdin.write(header + body)
    proc.stdin.flush()


def read_messages(proc: subprocess.Popen, timeout: float = 3.0) -> list[dict]:
    """从子进程 stdout 读取所有 JSON-RPC 消息，直到超时。"""
    messages: list[dict] = []
    deadline = time.monotonic() + timeout

    def reader():
        while time.monotonic() < deadline:
            # 读取 headers
            content_length = -1
            while True:
                line = proc.stdout.readline()
                if not line:
                    return
                line_str = line.decode("ascii", errors="replace").strip()
                if not line_str:
                    break
                if line_str.lower().startswith("content-length:"):
                    content_length = int(line_str.split(":", 1)[1].strip())

            if content_length < 0:
                continue

            body = proc.stdout.read(content_length)
            if body:
                messages.append(json.loads(body))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout=timeout + 0.5)
    return messages


def print_messages(messages: list[dict]) -> bool:
    """打印消息列表，返回是否包含 suggest。"""
    suggest_found = False
    for msg in messages:
        method = msg.get("method", "?")
        params = msg.get("params", {})

        if method == "nextEdit/status":
            print(f"    ← {method} (state={params.get('state', '')})")

        elif method == "nextEdit/suggest":
            suggest_found = True
            print(f"    ← {method}")
            print(f"      id:          {params.get('id', '')}")
            print(f"      uri:         {params.get('uri', '')}")
            print(f"      baseUri:     {params.get('baseUri', '')}")
            print(f"      baseVersion: {params.get('baseVersion', '')}")
            print(f"      description: {params.get('description', '')}")
            print(f"      deletedLines: {json.dumps(params.get('deletedLines', []), ensure_ascii=False)}")
            print(f"      addedLines:   {json.dumps(params.get('addedLines', []), ensure_ascii=False)}")

        elif method == "nextEdit/cancelSuggestion":
            print(f"    ← {method} (reason={params.get('reason', '')})")

        else:
            if "result" in msg:
                print(f"    ← response (id={msg.get('id')})")
            else:
                print(f"    ← {method}")

    return suggest_found


# ===========================================================================
# Scenario 1: Same-file rename propagation
# ===========================================================================

def verify_same_file(proc: subprocess.Popen) -> bool:
    """验证同文件 rename 传播。"""
    print("\n" + "-" * 60)
    print("场景 1：同文件 rename 传播")
    print("-" * 60)

    # didOpen: 打开包含 hello 函数和多处调用的文件
    print("\n  [1] 发送 didOpen（包含 hello 函数的 Python 文件）...")
    source_code = (
        "def hello(name):\n"
        "    return name\n"
        "\n"
        'hello("world")\n'
        'result = hello("test")\n'
    )
    send_message(proc, {
        "jsonrpc": "2.0",
        "method": "nextEdit/didOpen",
        "params": {
            "uri": "file:///tmp/test.py",
            "languageId": "python",
            "version": 1,
            "text": source_code,
        },
    })
    print("      → nextEdit/didOpen (v1)")
    time.sleep(0.3)

    # didChange: hello → goodbye
    print("\n  [2] 发送 didChange（hello → goodbye）...")
    send_message(proc, {
        "jsonrpc": "2.0",
        "method": "nextEdit/didChange",
        "params": {
            "uri": "file:///tmp/test.py",
            "version": 2,
            "changes": [{
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 9},
                },
                "text": "goodbye",
            }],
            "timestamp": int(time.time() * 1000),
        },
    })
    print("      → nextEdit/didChange (v2, hello→goodbye)")

    # 等待响应
    print("\n  [3] 等待服务端响应（最多 3 秒）...")
    responses = read_messages(proc, timeout=3.0)
    print(f"      收到 {len(responses)} 条消息：")
    suggest_found = print_messages(responses)

    # 关闭文件
    send_message(proc, {
        "jsonrpc": "2.0",
        "method": "nextEdit/didClose",
        "params": {"uri": "file:///tmp/test.py"},
    })
    time.sleep(0.2)

    if suggest_found:
        print("\n  ✓ PASS: 同文件 rename 传播正常")
    else:
        print("\n  ✗ FAIL: 未收到同文件 suggest")
    return suggest_found


# ===========================================================================
# Scenario 2: Cross-file rename propagation
# ===========================================================================

def verify_cross_file(proc: subprocess.Popen, workspace_root: str) -> bool:
    """验证跨文件 rename 传播。"""
    print("\n" + "-" * 60)
    print("场景 2：跨文件 rename 传播")
    print("-" * 60)

    # 等待 index 就绪（最多 15 秒）
    print("\n  [1] 等待项目索引就绪...")
    time.sleep(3.0)  # 给索引一些时间
    print("      索引应已就绪（3 个文件）")

    # didOpen: 打开 api.py
    api_path = os.path.join(workspace_root, "api.py")
    api_uri = f"file://{api_path}"
    with open(api_path) as f:
        api_content = f.read()

    print(f"\n  [2] 发送 didOpen（{api_uri}）...")
    send_message(proc, {
        "jsonrpc": "2.0",
        "method": "nextEdit/didOpen",
        "params": {
            "uri": api_uri,
            "languageId": "python",
            "version": 1,
            "text": api_content,
        },
    })
    print("      → nextEdit/didOpen (v1)")
    time.sleep(0.3)

    # didChange: hello → goodbye on line 0
    print("\n  [3] 发送 didChange（hello → goodbye）...")
    send_message(proc, {
        "jsonrpc": "2.0",
        "method": "nextEdit/didChange",
        "params": {
            "uri": api_uri,
            "version": 2,
            "changes": [{
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 9},
                },
                "text": "goodbye",
            }],
            "timestamp": int(time.time() * 1000),
        },
    })
    print("      → nextEdit/didChange (v2, hello→goodbye)")

    # 等待响应
    print("\n  [4] 等待服务端响应（最多 5 秒）...")
    responses = read_messages(proc, timeout=5.0)
    print(f"      收到 {len(responses)} 条消息：")
    suggest_found = print_messages(responses)

    # 验证跨文件：suggest 的 uri 应该不是 api.py
    cross_file_found = False
    for msg in responses:
        if msg.get("method") == "nextEdit/suggest":
            params = msg.get("params", {})
            if params.get("uri") != api_uri and params.get("baseUri") == api_uri:
                cross_file_found = True
                break

    if cross_file_found:
        print("\n  ✓ PASS: 跨文件 rename 传播正常（suggest 指向其他文件）")
    elif suggest_found:
        print("\n  △ PARTIAL: 收到 suggest 但不是跨文件（可能是同文件引用优先触发）")
    else:
        print("\n  ✗ FAIL: 未收到 suggest")

    return cross_file_found


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    print("=" * 60)
    print("NextOne Server 端到端验证")
    print("=" * 60)

    # 确定 playground 路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)  # server/
    repo_root = os.path.dirname(project_root)   # nextone/
    playground_dir = os.path.join(repo_root, "playground", "cross-file-rename")

    if not os.path.isdir(playground_dir):
        print(f"ERROR: playground 目录不存在: {playground_dir}")
        print("       请先创建 playground/cross-file-rename/ 验证文件")
        sys.exit(1)

    # 1. 启动服务端
    print(f"\n[启动] next-edit-server --stdio --workspace {playground_dir}")
    proc = subprocess.Popen(
        [
            "next-edit-server", "--stdio",
            "--log-level", "DEBUG",
            "--workspace", playground_dir,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        time.sleep(0.3)

        # LSP initialize handshake
        print("\n[握手] 发送 LSP initialize...")
        send_message(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "processId": None,
                "capabilities": {},
                "rootUri": f"file://{playground_dir}",
                "workspaceFolders": [
                    {"uri": f"file://{playground_dir}", "name": "cross-file-rename"}
                ],
            },
        })

        init_msgs = read_messages(proc, timeout=5.0)
        print(f"       收到 {len(init_msgs)} 条初始化消息：")
        for msg in init_msgs:
            method = msg.get("method", "")
            state = msg.get("params", {}).get("state", "")
            if "result" in msg:
                print(f"       ← initialize response (id={msg.get('id')})")
            elif method:
                print(f"       ← {method} (state={state})")

        # initialized notification
        send_message(proc, {
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {},
        })
        time.sleep(0.2)

        # Run verification scenarios
        pass1 = verify_same_file(proc)
        pass2 = verify_cross_file(proc, playground_dir)

        # Summary
        print("\n" + "=" * 60)
        print("验证结果汇总")
        print("=" * 60)
        print(f"  场景 1 (同文件 rename):  {'PASS ✓' if pass1 else 'FAIL ✗'}")
        print(f"  场景 2 (跨文件 rename):  {'PASS ✓' if pass2 else 'FAIL ✗'}")
        print("=" * 60)

        if pass1 and pass2:
            print("\n全部通过。")
        else:
            print("\n存在失败项，检查上方日志定位问题。")
            sys.exit(1)

    finally:
        proc.terminate()
        proc.wait(timeout=2)


if __name__ == "__main__":
    main()
