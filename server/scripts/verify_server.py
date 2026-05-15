"""验证 next-edit-server 端到端流程的脚本。

模拟 VS Code 客户端行为：
1. spawn next-edit-server --stdio 子进程
2. 发送 didOpen（打开一个包含 hello 函数的 Python 文件）
3. 发送 didChange（将 hello 重命名为 goodbye）
4. 读取服务端返回的所有消息，验证是否收到 suggest notification

用法：
    cd server
    pip install -e .
    python scripts/verify_server.py
"""

from __future__ import annotations

import json
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


def main() -> None:
    print("=" * 60)
    print("NextOne Server 端到端验证")
    print("=" * 60)

    # 1. 启动服务端
    print("\n[1] 启动 next-edit-server --stdio ...")
    proc = subprocess.Popen(
        ["next-edit-server", "--stdio", "--log-level", "DEBUG"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # 等待服务端初始化
        time.sleep(0.5)

        # 读取启动消息（loading_model → ready）
        init_msgs = read_messages(proc, timeout=2.0)
        print(f"    收到 {len(init_msgs)} 条初始化消息：")
        for msg in init_msgs:
            method = msg.get("method", "?")
            state = msg.get("params", {}).get("state", "")
            print(f"    ← {method} (state={state})")

        # 2. 发送 didOpen
        print("\n[2] 发送 didOpen（打开包含 hello 函数的 Python 文件）...")
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
        print("    → nextEdit/didOpen (v1)")
        time.sleep(0.3)

        # 3. 发送 didChange（将 hello 重命名为 goodbye）
        print("\n[3] 发送 didChange（hello → goodbye 重命名）...")
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
        print("    → nextEdit/didChange (v2, hello→goodbye)")

        # 4. 等待并读取服务端响应
        print("\n[4] 等待服务端响应（最多 3 秒）...")
        responses = read_messages(proc, timeout=3.0)

        print(f"    收到 {len(responses)} 条消息：")
        suggest_found = False
        for msg in responses:
            method = msg.get("method", "?")
            params = msg.get("params", {})

            if method == "nextEdit/status":
                print(f"    ← {method} (state={params.get('state', '')})")

            elif method == "nextEdit/suggest":
                suggest_found = True
                print(f"    ← {method}")
                print(f"      id:          {params.get('id', '')}")
                print(f"      uri:         {params.get('uri', '')}")
                print(f"      baseVersion: {params.get('baseVersion', '')}")
                print(f"      description: {params.get('description', '')}")
                print(f"      deletedLines: {json.dumps(params.get('deletedLines', []), ensure_ascii=False)}")
                print(f"      addedLines:   {json.dumps(params.get('addedLines', []), ensure_ascii=False)}")

            elif method == "nextEdit/cancelSuggestion":
                print(f"    ← {method} (reason={params.get('reason', '')})")

            else:
                print(f"    ← {method}")

        # 5. 验证结果
        print("\n" + "=" * 60)
        if suggest_found:
            print("PASS: 收到 nextEdit/suggest，服务端端到端流程正常。")
        else:
            print("FAIL: 未收到 nextEdit/suggest。")
            print("      检查 stderr 日志：")
            stderr_out = proc.stderr.read1(4096) if hasattr(proc.stderr, 'read1') else b""
            if stderr_out:
                print(stderr_out.decode("utf-8", errors="replace"))
        print("=" * 60)

    finally:
        proc.terminate()
        proc.wait(timeout=2)


if __name__ == "__main__":
    main()
