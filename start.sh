#!/bin/sh
# 启动顺序: api_v2 (9880, 仅容器内) -> 等就绪 -> MCP server (9881, 对外)
# 之后脚本留在前台当看门狗：任一进程退出，就结束另一个并以非 0 退出，
# 让平台（Docker restart 策略 / Zeabur 等）重启整个容器，而不是留下一个"MCP 活着、后端已死"的半残服务。
set -e
cd /app

# 参考音频必须先就位：没有它每次合成都会失败，不如在加载模型之前就停下，日志里写清楚原因
if ! python /app/prepare_ref.py; then
    echo "[start] reference audio not ready, see [prepare-ref] logs above" >&2
    exit 1
fi

# 子进程退出后、被 wait 回收前会变成僵尸，kill -0 对僵尸仍然返回成功，
# 所以存活判断要看 /proc/<pid>/stat 的状态字段，排除 Z。
alive() {
    st=$(cut -d' ' -f3 "/proc/$1/stat" 2>/dev/null)
    [ -n "$st" ] && [ "$st" != "Z" ]
}

python api_v2.py -a 127.0.0.1 -p 9880 > /tmp/api_v2.log 2>&1 &
API_PID=$!
echo "[start] api_v2 pid=$API_PID, waiting for port 9880 ..."

# 把 api_v2 的日志实时转到容器标准输出，平台日志面板里能直接看到推理报错
tail -n +1 -F /tmp/api_v2.log 2>/dev/null | sed -u 's/^/[api_v2] /' &
TAIL_PID=$!

i=0
until curl -s -o /dev/null "http://127.0.0.1:9880/docs"; do
    i=$((i + 1))
    if ! alive "$API_PID"; then
        echo "[start] api_v2 process died before ready" >&2
        sleep 1
        kill "$TAIL_PID" 2>/dev/null || true
        exit 1
    fi
    if [ "$i" -gt 240 ]; then
        echo "[start] api_v2 not ready after 240s" >&2
        kill "$API_PID" "$TAIL_PID" 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

echo "[start] api_v2 ready"

API_V2_PID=$API_PID python mcp_server.py &
MCP_PID=$!
echo "[start] mcp pid=$MCP_PID"

shutdown() {
    echo "[start] stopping: $1"
    kill "$MCP_PID" "$API_PID" 2>/dev/null || true
    wait "$MCP_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    kill "$TAIL_PID" 2>/dev/null || true
}

trap 'shutdown "received signal"; exit 0' TERM INT

set +e
while :; do
    if ! alive "$API_PID"; then
        echo "[start] api_v2 (pid=$API_PID) exited unexpectedly" >&2
        shutdown "api_v2 died"
        exit 1
    fi
    if ! alive "$MCP_PID"; then
        echo "[start] mcp_server (pid=$MCP_PID) exited unexpectedly" >&2
        shutdown "mcp_server died"
        exit 1
    fi
    sleep 5 &
    wait $!
done
