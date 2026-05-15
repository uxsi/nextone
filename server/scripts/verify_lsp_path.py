"""验证服务端在标准 LSP 消息路径下的行为。

模拟 vscode-languageclient 的完整行为：
1. initialize (request) → 等 response
2. initialized (notification)
3. textDocument/didOpen (标准 LSP 格式)
4. textDocument/didChange (标准 LSP 增量格式)
5. 验证是否收到 nextEdit/suggest
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time


def send_message(proc, msg):
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    proc.stdin.write(header + body)
    proc.stdin.flush()


def read_messages(proc, timeout=3.0):
    messages = []
    deadline = time.monotonic() + timeout

    def reader():
        while time.monotonic() < deadline:
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


def main():
    print("=" * 60)
    print("LSP 路径端到端验证")
    print("=" * 60)

    proc = subprocess.Popen(
        ["next-edit-server", "--stdio", "--log-level", "DEBUG"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        time.sleep(0.3)

        # Step 1: initialize (request)
        print("\n[1] initialize request...")
        send_message(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "processId": 12345,
                "capabilities": {},
                "rootUri": None,
            },
        })

        msgs = read_messages(proc, timeout=2.0)
        for m in msgs:
            if "result" in m:
                caps = m.get("result", {}).get("capabilities", {})
                print(f"    ← initialize response: textDocumentSync={caps.get('textDocumentSync')}")
            else:
                method = m.get("method", "?")
                state = m.get("params", {}).get("state", "")
                print(f"    ← {method} (state={state})")

        # Step 2: initialized (notification)
        print("\n[2] initialized notification...")
        send_message(proc, {
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {},
        })
        time.sleep(0.3)

        # Step 3: textDocument/didOpen (标准 LSP 格式)
        source = 'def hello(name):\n    return name\n\nhello("world")\nresult = hello("test")\n'
        print(f"\n[3] textDocument/didOpen (标准 LSP 格式)...")
        print(f"    文件内容：")
        for i, line in enumerate(source.splitlines()):
            print(f"      {i+1}: {line}")

        send_message(proc, {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": "file:///tmp/nextone.py",
                    "languageId": "python",
                    "version": 1,
                    "text": source,
                }
            },
        })
        time.sleep(0.3)

        # Step 4: textDocument/didChange (标准 LSP 增量格式)
        # 把第0行第4-9字符 "hello" 替换为 "hi"
        print(f"\n[4] textDocument/didChange (hello → hi)...")
        print(f"    range: (0,4)-(0,9), text='hi'")
        send_message(proc, {
            "jsonrpc": "2.0",
            "method": "textDocument/didChange",
            "params": {
                "textDocument": {
                    "uri": "file:///tmp/nextone.py",
                    "version": 2,
                },
                "contentChanges": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 9},
                        },
                        "rangeLength": 5,
                        "text": "hi",
                    }
                ],
            },
        })

        # Step 5: 等待 suggest
        print(f"\n[5] 等待 suggest（最多 5 秒）...")
        responses = read_messages(proc, timeout=5.0)

        suggest_found = False
        for m in responses:
            method = m.get("method", "?")
            params = m.get("params", {})
            if method == "nextEdit/suggest":
                suggest_found = True
                print(f"    ← nextEdit/suggest")
                print(f"      description: {params.get('description')}")
                print(f"      deletedLines: {params.get('deletedLines')}")
                print(f"      addedLines: {params.get('addedLines')}")
            elif method == "nextEdit/status":
                print(f"    ← nextEdit/status (state={params.get('state')})")
            else:
                print(f"    ← {method}")

        # 打印服务端 stderr 日志
        print(f"\n--- 服务端日志 (stderr) ---")
        time.sleep(0.5)
        try:
            stderr_data = proc.stderr.read1(8192).decode("utf-8", errors="replace")
            for line in stderr_data.strip().splitlines():
                print(f"  {line}")
        except Exception:
            pass

        print(f"\n{'=' * 60}")
        if suggest_found:
            print("PASS")
        else:
            print("FAIL: 未收到 nextEdit/suggest")
        print("=" * 60)

    finally:
        proc.terminate()
        proc.wait(timeout=2)


if __name__ == "__main__":
    main()
