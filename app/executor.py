import json
from dataclasses import dataclass, field
from e2b import AsyncSandbox

@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None = None
    replies: list[str] = field(default_factory=list)

REPLY_PREFIX = "::reply::"

def _extract_replies(stdout: str) -> list[str]:
    out = []
    for line in stdout.splitlines():
        if line.startswith(REPLY_PREFIX):
            try:
                obj = json.loads(line[len(REPLY_PREFIX):].strip())
                out.append(obj.get("text", ""))
            except json.JSONDecodeError:
                continue
    return out

class E2BExecutor:
    async def run(
        self,
        script: str,
        language: str,
        timeout_seconds: int,
        event_payload: dict | None = None,
        channel_type: str | None = None,
    ) -> ExecResult:
        sandbox = None
        try:
            sandbox = await AsyncSandbox.create(timeout=timeout_seconds + 15)

            path = "/tmp/script.py" if language == "python" else "/tmp/script.sh"
            await sandbox.files.write(path, script)

            envs = {
                "EVENT_PAYLOAD": json.dumps(event_payload or {}),
                "CHANNEL_TYPE": channel_type or "",
            }

            cmd = f"python3 {path}" if language == "python" else f"bash {path}"
            result = await sandbox.commands.run(cmd, timeout=timeout_seconds, envs=envs)

            stdout = (getattr(result, "stdout", "") or "")[:20_000]
            stderr = (getattr(result, "stderr", "") or "")[:20_000]

            return ExecResult(
                exit_code=getattr(result, "exit_code", None),
                stdout=stdout,
                stderr=stderr,
                replies=_extract_replies(stdout),
            )
        except Exception as e:
            return ExecResult(exit_code=None, stdout="", stderr="", error=f"{type(e).__name__}: {e}")
        finally:
            if sandbox is not None:
                try:
                    await sandbox.kill()
                except Exception:
                    pass

executor = E2BExecutor()