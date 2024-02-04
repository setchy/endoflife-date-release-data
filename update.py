import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from deepdiff import DeepDiff

from src.common.gha import GitHubOutput, GitHubStepSummary

SRC_DIR = Path('src')
DATA_DIR = Path('releases')


class ScriptResult:
    def __init__(self, path: Path, duration: float, success: bool) -> None:
        self.name = path.stem
        self.duration = duration
        self.success = success

    def __lt__(self, other: "ScriptResult") -> bool:
        return self.duration < other.duration


def run_scripts(summary: GitHubStepSummary) -> bool:
    results = []

    for script in sorted([SRC_DIR / file for file in os.listdir(SRC_DIR) if file.endswith('.py')]):
        logging.info(f"start running {script}")

        start = time.perf_counter()
        child = subprocess.run([sys.executable, script])  # timeout handled in subscripts
        elapsed_seconds = time.perf_counter() - start

        result = ScriptResult(script, elapsed_seconds, child.returncode == 0)
        log_level = logging.ERROR if not result.success else logging.INFO
        logging.log(log_level, f"ran {script}, took {elapsed_seconds:.2f}s (success={result.success})")
        results.append(result)

    summary.println("## Script execution summary\n")
    summary.println("| Name | Duration | Succeeded |")
    summary.println("|------|----------|-----------|")
    for result in sorted(results, reverse=True):
        summary.println(f"| {result.name} | {result.duration:.2f}s | {'✅' if result.success else '❌'} |")
    summary.println("")

    return not all(result.success for result in results)


def get_updated_products() -> list[Path]:
    subprocess.run('git add --all', timeout=10, check=True, shell=True)  # to also get new files in git diff
    git_diff = subprocess.run('git diff --name-only --staged', capture_output=True, timeout=10, check=True, shell=True)
    updated_files = [Path(file) for file in git_diff.stdout.decode('utf-8').split('\n')]
    return sorted([file for file in updated_files if file.parent == DATA_DIR])


def load_products_json(updated_product_files: list[Path]) -> dict[Path, dict]:
    files_content = {}

    for path in updated_product_files:
        if path.exists():
            with path.open() as file:
                files_content[path] = json.load(file)
        else:  # new or deleted file
            files_content[path] = {}

    return files_content


def generate_commit_message(old_content: dict[Path, dict], new_content: dict[Path, dict], summary: GitHubStepSummary) -> None:
    product_names = ', '.join([path.stem for path in old_content])
    summary.println(f"Updated {len(old_content)} products: {product_names}.\n")

    commit_message = GitHubOutput('commit_message')
    with commit_message:
        commit_message.println(f"🤖: {product_names}\n")

        for path in old_content:
            product_name = path.stem
            summary.println(f"### {product_name}\n")
            commit_message.println(f"{product_name}:")

            diff = DeepDiff(old_content[path], new_content[path], ignore_order=True, verbose_level=2)
            for line in diff.pretty().split('\n'):
                summary.println(f"- {line}")
                commit_message.println(f"- {line}")
                logging.info(f"{product_name}: {line}")

            commit_message.println("")
            summary.println("")


logging.basicConfig(format=logging.BASIC_FORMAT, level=logging.INFO)
with GitHubStepSummary() as step_summary:
    some_script_failed = run_scripts(step_summary)
    updated_products = get_updated_products()

    step_summary.println("## Update summary\n")
    if updated_products:
        new_files_content = load_products_json(updated_products)
        subprocess.run('git stash --all --quiet', timeout=10, check=True, shell=True)
        old_files_content = load_products_json(updated_products)
        subprocess.run('git stash pop --quiet', timeout=10, check=True, shell=True)
        generate_commit_message(old_files_content, new_files_content, step_summary)
    else:
        step_summary.println("No update")

sys.exit(1 if some_script_failed else 0)
