"""验证 import 关系过滤：不 import 源模块的文件不应出现在跨文件建议中。

场景：workspace 是整个 playground/，包含两个子目录：
  - cross-file-rename/api.py 定义 hello
  - cross-file-rename/test_api.py 和 cli.py import api（应该被建议）
  - cross-file-signature/client.py 包含 hello 但不 import api（不应被建议）

验证：rename api.py 中的 hello → goodbye，suggest 只指向 cross-file-rename/ 下的文件。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time


def send_message(proc, msg):
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    proc.stdin.write(header + body)
    proc.stdin.flush()


def read_messages(proc, timeout=5.0):
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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    repo_root = os.path.dirname(project_root)
    playground_dir = os.path.join(repo_root, "playground")

    print("=" * 60)
    print("验证 import 关系过滤")
    print("=" * 60)
    print(f"workspace: {playground_dir}")

    # 启动服务端，workspace 是整个 playground/
    proc = subprocess.Popen(
        ["next-edit-server", "--stdio", "--log-level", "DEBUG", "--workspace", playground_dir],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        time.sleep(0.3)

        # LSP initialize
        send_message(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "processId": None, "capabilities": {},
                "rootUri": f"file://{playground_dir}",
            },
        })
        init_msgs = read_messages(proc, timeout=5.0)
        print(f"\n初始化收到 {len(init_msgs)} 条消息")

        send_message(proc, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
        time.sleep(3.0)  # 等待索引完成

        # didOpen api.py
        api_path = os.path.join(playground_dir, "cross-file-rename", "api.py")
        api_uri = f"file://{api_path}"
        with open(api_path) as f:
            api_content = f.read()

        send_message(proc, {
            "jsonrpc": "2.0", "method": "nextEdit/didOpen",
            "params": {"uri": api_uri, "languageId": "python", "version": 1, "text": api_content},
        })
        time.sleep(0.3)

        # didChange: hello → goodbye
        send_message(proc, {
            "jsonrpc": "2.0", "method": "nextEdit/didChange",
            "params": {
                "uri": api_uri, "version": 2,
                "changes": [{"range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 9}}, "text": "goodbye"}],
                "timestamp": int(time.time() * 1000),
            },
        })

        responses = read_messages(proc, timeout=5.0)

        # 分析结果
        suggests = [m for m in responses if m.get("method") == "nextEdit/suggest"]
        print(f"\n收到 {len(suggests)} 条 suggest")

        bad_uris = []
        good_uris = []
        signature_dir = os.path.join(playground_dir, "cross-file-signature")
        for s in suggests:
            target_uri = s["params"]["uri"]
            target_path = target_uri.replace("file://", "")
            print(f"  → {target_uri}")
            if target_path.startswith(signature_dir):
                bad_uris.append(target_uri)
            else:
                good_uris.append(target_uri)

        print("\n" + "=" * 60)
        if bad_uris:
            print("FAIL: suggest 指向了不 import api 的文件：")
            for u in bad_uris:
                print(f"  ✗ {u}")
        elif not suggests:
            print("FAIL: 没有收到任何 suggest")
        else:
            print("PASS: 所有 suggest 都指向 import api 的文件")
            for u in good_uris:
                print(f"  ✓ {u}")
        print("=" * 60)

        sys.exit(1 if bad_uris or not suggests else 0)

    finally:
        proc.terminate()
        proc.wait(timeout=2)


if __name__ == "__main__":
    main()
