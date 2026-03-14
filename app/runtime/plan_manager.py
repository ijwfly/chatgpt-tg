import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from app.storage.db import DB


@dataclass
class PlanStep:
    id: str
    description: str
    status: str = "pending"  # "pending" | "in_progress" | "completed" | "skipped"


@dataclass
class Plan:
    db_id: int
    chat_id: int
    title: str
    steps: List[PlanStep]
    status: str = "active"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


VALID_STEP_STATUSES = {"pending", "in_progress", "completed", "skipped"}


class PlanManager:
    def __init__(self, db: DB, chat_id: int):
        self.db = db
        self.chat_id = chat_id
        self._plan: Optional[Plan] = None

    async def load(self) -> Optional[Plan]:
        """Load the active plan from DB."""
        record = await self.db.get_active_plan(self.chat_id)
        if record is None:
            self._plan = None
            return None

        steps_data = record['steps']
        if isinstance(steps_data, str):
            steps_data = json.loads(steps_data)

        steps = [PlanStep(id=s['id'], description=s['description'], status=s['status'])
                 for s in steps_data]
        self._plan = Plan(
            db_id=record['id'],
            chat_id=record['chat_id'],
            title=record['title'],
            steps=steps,
            status=record['status'],
            created_at=record['created_at'],
            updated_at=record['updated_at'],
        )
        return self._plan

    async def create_plan(self, title: str, steps: List[str]) -> str:
        """Create a new plan, deactivating any existing active plan."""
        # Deactivate existing active plan
        if self._plan is not None:
            await self.db.update_plan_status(self._plan.db_id, 'superseded')

        steps_data = [
            {'id': str(i + 1), 'description': desc, 'status': 'pending'}
            for i, desc in enumerate(steps)
        ]
        record = await self.db.create_plan(self.chat_id, title, steps_data)

        steps_objs = [PlanStep(id=s['id'], description=s['description'], status=s['status'])
                      for s in steps_data]
        self._plan = Plan(
            db_id=record['id'],
            chat_id=self.chat_id,
            title=title,
            steps=steps_objs,
            status='active',
            created_at=record['created_at'],
            updated_at=record['updated_at'],
        )
        return self._format_plan()

    async def update_step(self, step_id: str, status: str) -> str:
        """Update a plan step's status."""
        if self._plan is None:
            return "Error: No active plan"

        if status not in VALID_STEP_STATUSES:
            return f"Error: Invalid status '{status}'. Valid: {', '.join(VALID_STEP_STATUSES)}"

        step = next((s for s in self._plan.steps if s.id == step_id), None)
        if step is None:
            return f"Error: Unknown step {step_id}"

        step.status = status
        steps_data = [{'id': s.id, 'description': s.description, 'status': s.status}
                      for s in self._plan.steps]
        await self.db.update_plan_steps(self._plan.db_id, steps_data)

        # Auto-complete plan if all steps are completed or skipped
        all_done = all(s.status in ('completed', 'skipped') for s in self._plan.steps)
        if all_done:
            self._plan.status = 'completed'
            await self.db.update_plan_status(self._plan.db_id, 'completed')

        return self._format_plan()

    async def get_plan(self) -> str:
        """Get the current plan as formatted text."""
        if self._plan is None:
            return "No active plan."
        return self._format_plan()

    async def delete_plan(self) -> str:
        """Cancel the active plan."""
        if self._plan is None:
            return "No active plan to delete."
        await self.db.update_plan_status(self._plan.db_id, 'cancelled')
        self._plan = None
        return "Plan cancelled."

    def _format_plan(self) -> str:
        if self._plan is None:
            return "No active plan."

        status_icons = {
            'pending': '○',
            'in_progress': '►',
            'completed': '✓',
            'skipped': '—',
        }
        lines = [f"Plan: {self._plan.title} [{self._plan.status}]"]
        for step in self._plan.steps:
            icon = status_icons.get(step.status, '?')
            lines.append(f"  {icon} [{step.id}] {step.description} ({step.status})")
        return "\n".join(lines)
