import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional, Callable, Coroutine, Dict, List


@dataclass
class TaskInfo:
    task_id: str
    description: str
    status: str  # "running" | "completed" | "error" | "timeout"
    result: Optional[str] = None
    asyncio_task: Optional[asyncio.Task] = None


@dataclass
class TaskNotification:
    task_id: str
    description: str
    status: str
    result: str


class BackgroundTaskManager:
    def __init__(self, timeout: float = 300):
        self.tasks: Dict[str, TaskInfo] = {}
        self._notification_queue: asyncio.Queue = asyncio.Queue()
        self._timeout = timeout

    def spawn(self, coro: Coroutine, description: str) -> str:
        """Spawn an async task. Returns task_id immediately."""
        task_id = str(uuid.uuid4())[:8]
        info = TaskInfo(task_id=task_id, description=description, status="running")
        self.tasks[task_id] = info

        async def _wrapper():
            try:
                result = await asyncio.wait_for(coro, timeout=self._timeout)
                output = str(result)[:50000] if result else "(no output)"
                info.status = "completed"
                info.result = output
            except asyncio.TimeoutError:
                info.status = "timeout"
                info.result = f"Error: Timeout ({self._timeout}s)"
                output = info.result
            except asyncio.CancelledError:
                info.status = "cancelled"
                info.result = "Task cancelled"
                return  # Don't notify on cancellation
            except Exception as e:
                info.status = "error"
                info.result = f"Error: {e}"
                output = info.result

            await self._notification_queue.put(TaskNotification(
                task_id=task_id,
                description=description[:80],
                status=info.status,
                result=(output or "(no output)")[:2000],
            ))

        info.asyncio_task = asyncio.create_task(_wrapper())
        return task_id

    def check(self, task_id: str = None) -> str:
        """Check status of one task or list all."""
        if task_id:
            info = self.tasks.get(task_id)
            if not info:
                return f"Error: Unknown task {task_id}"
            result_str = info.result or '(running)'
            return f"[{info.status}] {info.description[:60]}\n{result_str}"

        if not self.tasks:
            return "No background tasks."

        lines = []
        for tid, info in self.tasks.items():
            lines.append(f"{tid}: [{info.status}] {info.description[:60]}")
        return "\n".join(lines)

    def drain_notifications(self) -> List[TaskNotification]:
        """Non-blocking drain of all completed task notifications."""
        notifications = []
        while True:
            try:
                notif = self._notification_queue.get_nowait()
                notifications.append(notif)
            except asyncio.QueueEmpty:
                break
        return notifications

    def has_pending(self) -> bool:
        """Check if any tasks are still running."""
        return any(info.status == "running" for info in self.tasks.values())

    async def wait_pending(self, timeout: float = 30) -> None:
        """Wait for all running tasks to complete, up to timeout."""
        pending = [
            info.asyncio_task for info in self.tasks.values()
            if info.status == "running" and info.asyncio_task is not None
        ]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    async def wait_for_any(self, timeout: float = None) -> None:
        """Wait for at least one running task to complete."""
        pending = [
            info.asyncio_task for info in self.tasks.values()
            if info.status == "running" and info.asyncio_task is not None
        ]
        if pending:
            timeout = timeout or self._timeout
            await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)

    async def cancel_all(self) -> None:
        """Cancel all running tasks."""
        for info in self.tasks.values():
            if info.status == "running" and info.asyncio_task is not None:
                info.asyncio_task.cancel()
                info.status = "cancelled"
