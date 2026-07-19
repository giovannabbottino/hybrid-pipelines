from __future__ import annotations

from pathlib import Path


class PromptRepository:
    def __init__(self, prompt_dir: Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parents[2]
        self.prompt_dir = prompt_dir or (base_dir / "prompt")

    def load_prompt(self, prompt_name: str) -> str:
        prompt_path = (self.prompt_dir / prompt_name).resolve()
        if self.prompt_dir.resolve() not in prompt_path.parents:
            raise ValueError(f"Prompt path {prompt_name!r} is outside the prompt directory.")
        if not prompt_path.is_file():
            raise FileNotFoundError(f"Prompt {prompt_name!r} not found at {prompt_path}.")
        return prompt_path.read_text(encoding="utf-8").strip()
