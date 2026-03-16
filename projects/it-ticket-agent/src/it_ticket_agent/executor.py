from typing import Any, Dict


class ActionExecutor:
    def execute(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if action == "rollback":
            return {
                "status": "success",
                "message": "已模拟执行回滚，请在生产中替换为受控 tool gateway",
                "params": params,
            }
        if action == "rollout_restart":
            return {
                "status": "success",
                "message": "已模拟执行重启",
                "params": params,
            }
        return {
            "status": "noop",
            "message": "无可执行动作",
            "params": params,
        }
